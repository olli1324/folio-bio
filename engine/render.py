"""Render a Briefing as Markdown.

Kept separate from the pipeline so the web layer can re-render a stored
briefing without re-running anything.
"""

from __future__ import annotations

import re

from engine.clinicaltrials import PHASE_LABEL
from engine.schema import Briefing, TrialInfo

# Matches the outer `[PMID:...]` bracket, then we pull every digit run from
# inside. This handles all variants we have seen from open models: a single
# PMID, comma-separated bare PMIDs, and the verbose `[PMID:1234, PMID:5678]`
# form where the prefix repeats per PMID.
_PMID_TAG = re.compile(r"\[\s*PMID[:\s]\s*([^\]]+?)\s*\]")
_PMID_DIGITS = re.compile(r"\d+")


def _trial_badge(info: TrialInfo | None) -> str:
    """Render a one-line ClinicalTrials.gov badge under a compound, or ''."""
    if info is None or info.total_trials == 0:
        return ""
    phase = PHASE_LABEL.get(info.highest_phase, "")
    parts: list[str] = []
    if phase:
        parts.append(f"**{phase}**")
    activity = []
    if info.active_trials:
        activity.append(f"{info.active_trials} active")
    if info.completed_trials:
        activity.append(f"{info.completed_trials} completed")
    if activity:
        parts.append(f"({', '.join(activity)} of {info.total_trials})")
    elif info.total_trials:
        parts.append(f"({info.total_trials} trials)")
    nct_links = " · ".join(
        f"[{n}](https://clinicaltrials.gov/study/{n})" for n in info.sample_ncts
    )
    if nct_links:
        parts.append(f"-- {nct_links}")
    return "_Trials:_ " + " ".join(parts)


def _link_pmids(text: str) -> str:
    """Turn inline `[PMID:1234]` tags into clickable PubMed Markdown links.

    Output stays clean Markdown so the downloadable `.md` is portable. The
    web UI's JS upgrades these into Folio `.f-pmid`-styled chips at render
    time (see static/app.js).
    """
    def repl(match: re.Match) -> str:
        pmids = _PMID_DIGITS.findall(match.group(1))
        if not pmids:
            return match.group(0)
        links = [
            f"[PMID {p}](https://pubmed.ncbi.nlm.nih.gov/{p}/)" for p in pmids
        ]
        return " ".join(links)

    return _PMID_TAG.sub(repl, text)


def briefing_to_markdown(briefing: Briefing) -> str:
    """Format a Briefing as a readable Markdown document."""
    lines: list[str] = [f"# Research Briefing - {briefing.query}", ""]

    if briefing.summary:
        lines += ["## Summary", "", _link_pmids(briefing.summary), ""]

    if briefing.top_compounds:
        lines += ["## Top compound candidates", ""]
        for i, compound in enumerate(briefing.top_compounds, start=1):
            lines.append(f"{i}. **{compound.name}**")
            if compound.rationale:
                lines.append(f"   {_link_pmids(compound.rationale)}")
            badge = _trial_badge(compound.trial_info)
            if badge:
                lines.append(f"   {badge}")
            if compound.supporting_pmids:
                pmids = ", ".join(compound.supporting_pmids)
                lines.append(f"   Supporting PMIDs: {pmids}")
            lines.append("")

    if briefing.mechanisms:
        lines += ["## Mechanisms of action", ""]
        lines += [f"- {m}" for m in briefing.mechanisms]
        lines.append("")

    if briefing.key_papers:
        lines += ["## Key papers", ""]
        for paper in briefing.key_papers:
            lines.append(f"- [{paper.citation}]({paper.url})")
        lines.append("")

    if briefing.open_questions:
        lines += ["## Open questions", ""]
        lines += [f"- {_link_pmids(q)}" for q in briefing.open_questions]
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
