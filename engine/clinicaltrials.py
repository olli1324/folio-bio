"""ClinicalTrials.gov v2 API wrapper for compound enrichment.

For each top compound in the briefing we ask "how far has this gone in
trials?" and surface a small badge on the briefing (e.g. *Phase III, 8 active,
12 completed; NCT02296125 …*). It turns the briefing from a literature summary
into something closer to an intel report.

No auth required, no documented hard rate limit; we still cap concurrent
in-flight requests and time them out conservatively.
"""

from __future__ import annotations

import asyncio
from collections import Counter

import httpx

from engine.schema import TrialInfo

_BASE = "https://clinicaltrials.gov/api/v2"

# Higher index = more advanced phase. Anything not in this list ranks 0.
_PHASE_ORDER = [
    "",
    "EARLY_PHASE1",
    "PHASE1",
    "PHASE1_PHASE2",
    "PHASE2",
    "PHASE2_PHASE3",
    "PHASE3",
    "PHASE4",
]

# Statuses we count as "active". Anything else counts only into total_trials
# unless it's COMPLETED, which we report separately so readers can tell
# matured vs ongoing programs at a glance.
_ACTIVE_STATUSES = {"RECRUITING", "ACTIVE_NOT_RECRUITING", "ENROLLING_BY_INVITATION"}

# Compact display names used by render.py.
PHASE_LABEL = {
    "EARLY_PHASE1": "Early Phase I",
    "PHASE1": "Phase I",
    "PHASE1_PHASE2": "Phase I/II",
    "PHASE2": "Phase II",
    "PHASE2_PHASE3": "Phase II/III",
    "PHASE3": "Phase III",
    "PHASE4": "Phase IV",
}


class ClinicalTrialsError(RuntimeError):
    """Raised when a request to ClinicalTrials.gov fails."""


class ClinicalTrialsClient:
    """Async wrapper around the ClinicalTrials.gov v2 /studies endpoint."""

    def __init__(
        self,
        *,
        concurrency: int = 5,
        timeout: float = 15.0,
        page_size: int = 20,
    ) -> None:
        self._sem = asyncio.Semaphore(concurrency)
        self._timeout = timeout
        self._page_size = page_size

    async def enrich(
        self, compound: str, query_hint: str = ""
    ) -> TrialInfo | None:
        """Look up trials for one compound and aggregate a TrialInfo.

        Returns None on hard failure (network error, unparseable response).
        Returns a TrialInfo with zero counts and an empty highest_phase when
        the compound has no matches -- the renderer hides that case.
        """
        params = {
            "query.intr": compound,
            "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
            "pageSize": str(self._page_size),
            "sort": "LastUpdatePostDate:desc",
            "fields": "|".join(
                [
                    "protocolSection.identificationModule.nctId",
                    "protocolSection.designModule.phases",
                    "protocolSection.statusModule.overallStatus",
                    "protocolSection.statusModule.lastUpdatePostDateStruct",
                ]
            ),
        }
        if query_hint:
            params["query.term"] = query_hint

        async with self._sem:
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(f"{_BASE}/studies", params=params)
                    resp.raise_for_status()
                    data = resp.json()
            except (httpx.HTTPError, ValueError):
                return None

        return _aggregate(data.get("studies", []))


def _aggregate(studies: list[dict]) -> TrialInfo:
    """Roll up a list of /studies response items into one TrialInfo."""
    phases_seen: list[str] = []
    statuses: Counter[str] = Counter()
    ncts: list[str] = []

    for s in studies:
        ps = s.get("protocolSection", {})
        nct = ps.get("identificationModule", {}).get("nctId") or ""
        if nct:
            ncts.append(nct)
        for phase in ps.get("designModule", {}).get("phases", []) or []:
            phases_seen.append(phase)
        status = ps.get("statusModule", {}).get("overallStatus") or ""
        if status:
            statuses[status] += 1

    highest_phase = ""
    for phase in phases_seen:
        if _phase_rank(phase) > _phase_rank(highest_phase):
            highest_phase = phase

    active = sum(statuses[s] for s in _ACTIVE_STATUSES)
    completed = statuses.get("COMPLETED", 0)

    return TrialInfo(
        highest_phase=highest_phase,
        active_trials=active,
        completed_trials=completed,
        total_trials=len(studies),
        sample_ncts=ncts[:3],
    )


def _phase_rank(phase: str) -> int:
    try:
        return _PHASE_ORDER.index(phase)
    except ValueError:
        return 0
