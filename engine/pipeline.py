"""The Folio pipeline.

Seven nodes, run in sequence, with a fan-out at the extraction step and a
parallel enrichment over top compounds:

    plan -> search -> retrieve -> extract -> synthesise -> enrich -> format

The public entry point is `run_pipeline`, an async generator that yields
`ProgressEvent`s as it goes. The web layer turns those into SSE messages; the
CLI prints them. `node_extract` and `node_enrich_trials` are themselves async
generators that emit one event per completed item, so the live trace ticks
through fan-outs instead of stalling between stages.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

from engine import prompts
from engine.clinicaltrials import ClinicalTrialsClient
from engine.featherless import FeatherlessClient
from engine.pubmed import PubMedClient
from engine.render import briefing_to_markdown
from engine.schema import (
    Briefing,
    CompoundCandidate,
    Extraction,
    Paper,
    PipelineState,
    ProgressEvent,
    Stage,
)

# How many of the most relevant papers to attach to the briefing.
_KEY_PAPER_COUNT = 8

# Process-wide default clients. Featherless bills by concurrent slots, so a
# single shared FeatherlessClient is required for the 4-slot budget claim to
# hold across multiple in-flight pipeline runs (e.g. voice + web both
# triggering a query). The web/voice layers should reuse these via
# `get_default_clients()` rather than constructing their own.
_DEFAULT_FL: FeatherlessClient | None = None
_DEFAULT_PM: PubMedClient | None = None
_DEFAULT_CT: ClinicalTrialsClient | None = None


def get_default_clients() -> tuple[FeatherlessClient, PubMedClient, ClinicalTrialsClient]:
    """Return process-wide singleton clients, lazily constructed.

    Importers that run multiple pipelines concurrently must use this rather
    than instantiating their own clients, otherwise Featherless will see more
    in-flight calls than the slot budget allows and start rate-limiting.
    """
    global _DEFAULT_FL, _DEFAULT_PM, _DEFAULT_CT
    if _DEFAULT_FL is None:
        _DEFAULT_FL = FeatherlessClient()
    if _DEFAULT_PM is None:
        _DEFAULT_PM = PubMedClient()
    if _DEFAULT_CT is None:
        _DEFAULT_CT = ClinicalTrialsClient()
    return _DEFAULT_FL, _DEFAULT_PM, _DEFAULT_CT


# --- nodes -----------------------------------------------------------------

async def node_plan(state: PipelineState, fl: FeatherlessClient) -> PipelineState:
    """Expand the raw query into PubMed search terms."""
    data = await fl.chat_json(prompts.plan_messages(state.query), fl.extract_model)
    terms = data.get("search_terms") or []
    terms = [str(t).strip() for t in terms if str(t).strip()]
    # Always fall back to the raw query so search never runs empty.
    state.search_terms = terms or [state.query]
    return state


async def node_search(
    state: PipelineState, pm: PubMedClient, *, max_papers: int = 25
) -> PipelineState:
    """Run ESearch and collect PMIDs."""
    state.pmids = await pm.search(state.search_terms, retmax=max_papers)
    return state


async def node_retrieve(state: PipelineState, pm: PubMedClient) -> PipelineState:
    """Pull titles, abstracts and metadata for the PMIDs."""
    state.papers = await pm.fetch(state.pmids)
    return state


async def node_extract(
    state: PipelineState, fl: FeatherlessClient
) -> AsyncIterator[ProgressEvent]:
    """Per-paper extraction, fanned out, yielding a progress event per paper.

    Async generator so each completion surfaces in real time -- the web layer
    and the live pitch see the fan-out tick along instead of one blocking
    pause. The Featherless client's semaphore still caps how many calls are in
    flight, so creating tasks up-front is safe: only the slot budget gets
    work, the rest queue inside the client. State is mutated in place at the
    end; the caller does not need a return value.
    """
    total = len(state.papers)
    results: list[Extraction | None] = [None] * total

    async def extract_one(idx: int, paper: Paper) -> tuple[int, Paper]:
        try:
            data = await fl.chat_json(
                prompts.extract_messages(state.query, paper), fl.extract_model
            )
            results[idx] = _build_extraction(paper.pmid, data)
        except Exception:
            # One bad paper should not sink the run.
            results[idx] = None
        return idx, paper

    tasks = [
        asyncio.create_task(extract_one(i, p)) for i, p in enumerate(state.papers)
    ]
    completed = 0
    try:
        for task in asyncio.as_completed(tasks):
            idx, paper = await task
            completed += 1
            ok = results[idx] is not None
            suffix = "" if ok else " (skipped)"
            yield ProgressEvent(
                stage=Stage.EXTRACT,
                message=f"Extracted {completed}/{total}: PMID {paper.pmid}{suffix}",
                detail={
                    "pmid": paper.pmid,
                    "completed": completed,
                    "total": total,
                    "ok": ok,
                    "extraction": (
                        results[idx].model_dump() if results[idx] is not None else None
                    ),
                },
            )
    finally:
        # If the consumer breaks out of the `async for` early (e.g. the web
        # client disconnects), cancel any in-flight extractions and drain so
        # we do not leak tasks or "exception was never retrieved" warnings.
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    state.extractions = [r for r in results if r is not None]


async def node_synthesise(
    state: PipelineState, fl: FeatherlessClient
) -> PipelineState:
    """Cluster and rank across all extractions into a Briefing.

    Falls back to a minimal Briefing assembled directly from the extractions
    if the synthesis model returns unparseable or empty output. The demo must
    never show a hard failure mid-stage if there is salvageable signal.
    """
    if not state.extractions:
        state.briefing = Briefing(
            query=state.query,
            summary="No papers could be extracted for this query.",
        )
        return state

    try:
        data = await fl.chat_json(
            prompts.synthesis_messages(state.query, state.extractions),
            fl.synthesis_model,
            max_tokens=2048,
        )
    except Exception:
        state.briefing = _fallback_briefing(state)
        return state

    compounds = [
        CompoundCandidate(
            name=str(c.get("name", "")).strip(),
            rationale=str(c.get("rationale", "")).strip(),
            supporting_pmids=[
                _clean_pmid(p) for p in _as_list(c.get("supporting_pmids"))
                if _clean_pmid(p)
            ],
        )
        for c in _as_list(data.get("top_compounds"))
        if isinstance(c, dict) and str(c.get("name", "")).strip()
    ]
    # Apply deterministic alias collapse so the synthesis layer's instruction
    # is not the sole defence against duplicates like osimertinib vs AZD9291.
    compounds = _collapse_aliases(compounds)

    state.briefing = Briefing(
        query=state.query,
        summary=str(data.get("summary", "")).strip(),
        top_compounds=compounds,
        mechanisms=[
            str(m).strip() for m in _as_list(data.get("mechanisms")) if str(m).strip()
        ],
        key_papers=_key_papers(state),
        open_questions=[
            str(q).strip() for q in _as_list(data.get("open_questions")) if str(q).strip()
        ],
    )
    return state


async def node_enrich_trials(
    state: PipelineState, ct: ClinicalTrialsClient
) -> AsyncIterator[ProgressEvent]:
    """Annotate each top compound with a ClinicalTrials.gov badge.

    Runs as an async generator so each enrichment surfaces a progress event
    as it lands -- judges and the web layer see the agent doing a second
    pass over external data, not stalling between synthesis and the final
    briefing. Failures per compound are swallowed silently; the badge just
    will not render in the Markdown for that compound.
    """
    if state.briefing is None or not state.briefing.top_compounds:
        return

    total = len(state.briefing.top_compounds)

    async def enrich_one(idx: int, compound: CompoundCandidate) -> int:
        info = await ct.enrich(compound.name, state.query)
        if info is not None:
            compound.trial_info = info
        return idx

    tasks = [
        asyncio.create_task(enrich_one(i, c))
        for i, c in enumerate(state.briefing.top_compounds)
    ]
    completed = 0
    try:
        for task in asyncio.as_completed(tasks):
            idx = await task
            completed += 1
            compound = state.briefing.top_compounds[idx]
            info = compound.trial_info
            if info and info.total_trials:
                blurb = (
                    f"{compound.name}: "
                    f"{info.highest_phase or 'no phase'}, "
                    f"{info.total_trials} trial(s)"
                )
            else:
                blurb = f"{compound.name}: no interventional trials matched"
            yield ProgressEvent(
                stage=Stage.ENRICH,
                message=f"Enriched {completed}/{total} -- {blurb}",
                detail={
                    "compound": compound.name,
                    "trial_info": (info.model_dump() if info else None),
                },
            )
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def node_format(state: PipelineState) -> PipelineState:
    """Render the briefing as Markdown alongside the structured object."""
    if state.briefing is not None:
        state.briefing_markdown = briefing_to_markdown(state.briefing)
    return state


# --- orchestration ---------------------------------------------------------

async def run_pipeline(
    query: str,
    *,
    max_papers: int | None = None,
    fl: FeatherlessClient | None = None,
    pm: PubMedClient | None = None,
    ct: ClinicalTrialsClient | None = None,
) -> AsyncIterator[ProgressEvent]:
    """Run the full pipeline, yielding a ProgressEvent after each stage.

    Pass your own `fl` / `pm` / `ct` to reuse clients or inject test doubles;
    otherwise they are constructed from environment variables (or defaults
    for ClinicalTrials.gov, which needs no auth). `max_papers` caps the
    PubMed search; defaults to the `MAX_PAPERS` env var (or 25).
    """
    if max_papers is None:
        max_papers = int(os.getenv("MAX_PAPERS", "25"))

    state = PipelineState(query=query)
    try:
        if fl is None or pm is None or ct is None:
            default_fl, default_pm, default_ct = get_default_clients()
            fl = fl or default_fl
            pm = pm or default_pm
            ct = ct or default_ct

        yield ProgressEvent(stage=Stage.PLAN, message=f"Planning search for: {query}")
        state = await node_plan(state, fl)
        yield ProgressEvent(
            stage=Stage.PLAN,
            message=f"Search terms: {', '.join(state.search_terms)}",
            detail={"search_terms": state.search_terms},
        )

        yield ProgressEvent(stage=Stage.SEARCH, message="Searching PubMed...")
        state = await node_search(state, pm, max_papers=max_papers)
        yield ProgressEvent(
            stage=Stage.SEARCH,
            message=f"Found {len(state.pmids)} papers",
            detail={"pmid_count": len(state.pmids)},
        )
        if not state.pmids:
            state.error = "PubMed returned no results for this query."
            yield ProgressEvent(stage=Stage.ERROR, message=state.error)
            return

        yield ProgressEvent(stage=Stage.RETRIEVE, message="Retrieving abstracts...")
        state = await node_retrieve(state, pm)
        yield ProgressEvent(
            stage=Stage.RETRIEVE,
            message=f"Retrieved {len(state.papers)} records",
            detail={
                "paper_count": len(state.papers),
                # Full paper metadata so a UI consumer can render the papers
                # sidebar before extraction starts ticking.
                "papers": [
                    {
                        "pmid": p.pmid,
                        "title": p.title,
                        "abstract": p.abstract,
                        "authors": p.authors,
                        "journal": p.journal,
                        "year": p.year,
                        "url": p.url,
                    }
                    for p in state.papers
                ],
            },
        )

        yield ProgressEvent(
            stage=Stage.EXTRACT,
            message=f"Extracting from {len(state.papers)} papers...",
        )
        async for ev in node_extract(state, fl):
            yield ev
        yield ProgressEvent(
            stage=Stage.EXTRACT,
            message=f"Extracted {len(state.extractions)} of {len(state.papers)} papers",
            detail={"extraction_count": len(state.extractions)},
        )

        yield ProgressEvent(stage=Stage.SYNTHESISE, message="Synthesising briefing...")
        state = await node_synthesise(state, fl)
        yield ProgressEvent(stage=Stage.SYNTHESISE, message="Briefing drafted")

        if state.briefing and state.briefing.top_compounds:
            yield ProgressEvent(
                stage=Stage.ENRICH,
                message=f"Enriching {len(state.briefing.top_compounds)} compounds from ClinicalTrials.gov...",
            )
            async for ev in node_enrich_trials(state, ct):
                yield ev

        state = await node_format(state)
        yield ProgressEvent(
            stage=Stage.DONE,
            message="Briefing ready",
            detail={
                "briefing": state.briefing.model_dump() if state.briefing else {},
                "briefing_markdown": state.briefing_markdown or "",
            },
        )
    except Exception as exc:  # noqa: BLE001 - surface any failure as an event
        state.error = str(exc)
        yield ProgressEvent(stage=Stage.ERROR, message=str(exc))


# --- helpers ---------------------------------------------------------------

# Deterministic alias collapse: development code names and pre-approval IDs
# map to the approved generic name. Synthesis is also prompted to do this, but
# open models drop it inconsistently; a small hardcoded table is the only
# robust fix. Keys are normalised (lowercase, no separators); values are the
# canonical name as the briefing should display it.
_ALIASES: dict[str, str] = {
    "azd9291": "osimertinib",
    "mk3475": "pembrolizumab",
    "mpdl3280a": "atezolizumab",
    "medi4736": "durvalumab",
    "msb0010718c": "avelumab",
    "ly2157299": "galunisertib",
    "ly3537982": "olomorasib",
    "rg6330": "divarasib",
    "rmc6236": "daraxonrasib",
    "amg510": "sotorasib",
    "mrtx849": "adagrasib",
    "blu945": "blu-945",
    "azd9150": "danvatirsen",
}


def _normalize_compound(name: str) -> str:
    """Lowercase + strip hyphens/spaces for alias-table lookup."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _collapse_aliases(compounds: list[CompoundCandidate]) -> list[CompoundCandidate]:
    """Merge duplicate compounds whose names are known aliases.

    Two compounds collapse when their normalised names map to the same
    canonical key. Supporting PMIDs are unioned, the longer rationale wins.
    """
    by_canonical: dict[str, CompoundCandidate] = {}
    for c in compounds:
        canon = _ALIASES.get(_normalize_compound(c.name), c.name.lower())
        if canon in by_canonical:
            existing = by_canonical[canon]
            # Union PMIDs preserving order.
            seen = set(existing.supporting_pmids)
            for p in c.supporting_pmids:
                if p not in seen:
                    existing.supporting_pmids.append(p)
                    seen.add(p)
            # Keep the rationale with more content.
            if len(c.rationale) > len(existing.rationale):
                existing.rationale = c.rationale
        else:
            # If the model used the dev code name but a canonical exists,
            # rename to canonical so the badge looks professional.
            display_name = _ALIASES.get(_normalize_compound(c.name), c.name)
            c.name = display_name
            by_canonical[canon] = c
    return list(by_canonical.values())


def _as_list(value: object) -> list:
    """Coerce a model JSON field to a list defensively.

    Open models occasionally return a string where a list is expected, or
    drop the field entirely. Convert single strings to a singleton list,
    other non-list values to empty.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [value]
    return []


def _fallback_briefing(state: PipelineState) -> Briefing:
    """Assemble a minimal Briefing directly from extractions.

    Used when the synthesis model fails (empty string, unparseable JSON,
    or a 5xx that exhausted retries). Picks the most frequent compounds
    and mechanisms across extractions so the user still gets a usable
    briefing instead of a hard error on the live demo.
    """
    from collections import Counter

    compound_count: Counter[str] = Counter()
    compound_pmids: dict[str, list[str]] = {}
    mechanism_count: Counter[str] = Counter()
    for e in state.extractions:
        for c in e.compounds:
            key = c.strip()
            if not key:
                continue
            compound_count[key] += 1
            compound_pmids.setdefault(key, []).append(e.pmid)
        if e.mechanism_of_action.strip():
            mechanism_count[e.mechanism_of_action.strip()] += 1

    top = [
        CompoundCandidate(
            name=name,
            rationale=(
                f"Mentioned in {count} paper{'s' if count != 1 else ''}; "
                "synthesis model could not produce a structured rationale."
            ),
            supporting_pmids=compound_pmids.get(name, []),
        )
        for name, count in compound_count.most_common(5)
    ]

    return Briefing(
        query=state.query,
        summary=(
            "Synthesis model returned no structured response; "
            "this briefing was assembled directly from the per-paper extractions."
        ),
        top_compounds=_collapse_aliases(top),
        mechanisms=[m for m, _ in mechanism_count.most_common(5)],
        key_papers=_key_papers(state),
        open_questions=[],
    )


def _clean_pmid(raw: object) -> str:
    """Strip any `PMID:` / `PMID ` prefix and surrounding whitespace.

    The synthesis model is inconsistent about prefixing PMIDs in the
    structured `supporting_pmids` list. Normalise to the bare numeric string
    so downstream rendering and any joins to PubMed URLs are clean.
    """
    s = str(raw).strip()
    # Cheap case-insensitive prefix strip without compiling a regex.
    if s.lower().startswith("pmid"):
        s = s[4:].lstrip(": \t")
    return s.strip()


_VALID_STUDY_TYPES = {"preclinical", "clinical", "review", "real-world", "unknown"}


# Patterns that match drug-class strings (not specific compounds). Used as
# a defensive filter in `_build_extraction` to catch class-name leakage
# that the extract prompt's strict-rules section is meant to prevent.
# Conservative: each pattern must clearly indicate a class rather than a
# specific molecule.
import re as _re

_DRUG_CLASS_PATTERNS = [
    _re.compile(r"^anti[\s-]", _re.IGNORECASE),                # anti-PD-1, anti-HER2
    _re.compile(r"\binhibitors?\b", _re.IGNORECASE),           # PD-1 inhibitor(s)
    _re.compile(r"-tkis?\b", _re.IGNORECASE),                  # EGFR-TKI(s)
    _re.compile(r"[-\s]targeted\b", _re.IGNORECASE),           # HER2-targeted
    _re.compile(r"\bblockade\b", _re.IGNORECASE),              # PD-1 blockade
    _re.compile(r"\bcheckpoint\b", _re.IGNORECASE),            # checkpoint inhibitor / blockade
    _re.compile(r"\bantibod(?:y|ies)\b", _re.IGNORECASE),      # monoclonal antibodies (class)
    _re.compile(r"\bchemotherapy\b", _re.IGNORECASE),          # generic chemo
    _re.compile(r"\bagents?\b", _re.IGNORECASE),               # "EGFR-targeted agents"
    _re.compile(r"\btherap(?:y|ies)\b", _re.IGNORECASE),       # "targeted therapy"
]


def _is_drug_class(name: str) -> bool:
    """True if `name` looks like a drug class rather than a specific compound."""
    return any(p.search(name) for p in _DRUG_CLASS_PATTERNS)


def _build_extraction(pmid: str, data: dict) -> Extraction:
    """Coerce a model's JSON into a validated Extraction."""
    raw_score = data.get("relevance_score", 0.0)
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        score = 0.0
    score = min(1.0, max(0.0, score))

    compounds = _as_list(data.get("compounds"))

    study_type = str(data.get("study_type", "")).strip().lower()
    if study_type not in _VALID_STUDY_TYPES:
        # Model returned something off-vocabulary; preserve free-text rather
        # than discard the signal entirely.
        study_type = study_type

    # Filter out drug-class strings that leak through the prompt's rules
    # (e.g. "PD-1 inhibitor", "anti-PD-L1"). Specific compounds always pass.
    cleaned_compounds = [
        str(c).strip()
        for c in compounds
        if str(c).strip() and not _is_drug_class(str(c).strip())
    ]

    return Extraction(
        pmid=pmid,
        compounds=cleaned_compounds,
        mechanism_of_action=str(data.get("mechanism_of_action", "")).strip(),
        key_finding=str(data.get("key_finding", "")).strip(),
        relevance_score=score,
        variant_or_mutation=str(data.get("variant_or_mutation", "")).strip(),
        potency=str(data.get("potency", "")).strip(),
        selectivity=str(data.get("selectivity", "")).strip(),
        study_type=study_type,
        resistance_mechanism=str(data.get("resistance_mechanism", "")).strip(),
    )


def _key_papers(state: PipelineState) -> list[Paper]:
    """The most relevant papers, by extraction score, for the briefing."""
    by_pmid = {p.pmid: p for p in state.papers}
    ranked = sorted(
        state.extractions, key=lambda e: e.relevance_score, reverse=True
    )
    picked = []
    for extraction in ranked[:_KEY_PAPER_COUNT]:
        paper = by_pmid.get(extraction.pmid)
        if paper is not None:
            picked.append(paper)
    return picked
