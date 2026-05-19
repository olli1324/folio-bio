"""Quality evaluations for Folio briefings.

Two checks complement the recall benchmark in `evals.py`:

  - **Extraction precision** — for every compound the extractor names from a
    paper, verify the compound actually appears in that paper's title or
    abstract. Catches the model inventing molecules from outside the source.

  - **Citation grounding** — every `[PMID:NNN]` tag the synthesis model
    embeds in the summary must point to a PMID we actually retrieved and
    extracted in this run. Catches fabricated citations.

Both run by driving the same pipeline as the main benchmark and inspecting
the final `PipelineState`. Run from the repo root:

    python evals_quality.py

Prints a Markdown table at the end for the README.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from dotenv import load_dotenv

from engine.pipeline import (
    get_default_clients,
    node_enrich_trials,
    node_extract,
    node_format,
    node_plan,
    node_retrieve,
    node_search,
    node_synthesise,
)
from engine.schema import PipelineState


# Five canonical drug-discovery queries (same set as recall benchmark in
# evals.py). Smaller per-query paper count keeps the run under ~10 minutes
# while still surfacing meaningful precision numbers.
QUERIES: list[str] = [
    "EGFR inhibitors for non-small-cell lung cancer",
    "BRAF V600E melanoma",
    "KRAS G12C inhibitors for solid tumors",
    "HER2 positive breast cancer",
    "PD-L1 checkpoint inhibitors for cancer",
]
PAPERS_PER_QUERY = 15  # smaller than the 25 used for recall, faster turnaround


# --- runners --------------------------------------------------------------


async def run_pipeline_to_state(query: str, max_papers: int) -> PipelineState:
    """Drive the seven pipeline nodes manually and return the final state.

    `run_pipeline` is an async generator that yields events; for the eval
    we just want the terminal state object, so we replicate its sequence
    here without the event yield-points.
    """
    fl, pm, ct = get_default_clients()
    state = PipelineState(query=query)
    state = await node_plan(state, fl)
    state = await node_search(state, pm, max_papers=max_papers)
    if not state.pmids:
        return state
    state = await node_retrieve(state, pm)
    # node_extract is itself an async generator; drain it.
    async for _ in node_extract(state, fl):
        pass
    state = await node_synthesise(state, fl)
    if state.briefing and state.briefing.top_compounds:
        async for _ in node_enrich_trials(state, ct):
            pass
    state = await node_format(state)
    return state


# --- extraction precision -------------------------------------------------


def _normalize_for_match(text: str) -> str:
    """Lowercase, collapse hyphens / whitespace, strip punctuation.

    Used to make substring matching tolerant of stylistic differences
    between how a paper writes a compound (e.g. `T-DM1`, `T DM1`) and how
    the model surfaces it (`T-DM1`, `T DM1`, `TDM1`).
    """
    return re.sub(r"[\s\-/_.,;:()]+", " ", text.lower()).strip()


def _compound_appears_in_paper(compound: str, paper_text: str) -> bool:
    """Tolerant substring check across normalized strings."""
    c = _normalize_for_match(compound)
    if not c or len(c) < 3:
        # Too short to match safely (e.g. "EZ"). Treat as unverifiable.
        return False
    haystack = _normalize_for_match(paper_text)
    if c in haystack:
        return True
    # Also try a tight hyphenless form ("T-DM1" -> "tdm1") in case the
    # normalised haystack still keeps spaces.
    c_tight = c.replace(" ", "")
    if c_tight and c_tight in haystack.replace(" ", ""):
        return True
    return False


def evaluate_extraction_precision(state: PipelineState) -> dict[str, Any]:
    """Score (verified compounds) / (total extracted compounds) across all papers."""
    papers_by_pmid = {p.pmid: p for p in state.papers}
    total = 0
    verified = 0
    misses: list[dict[str, str]] = []
    for ex in state.extractions:
        paper = papers_by_pmid.get(ex.pmid)
        if paper is None:
            continue
        text = (paper.title or "") + " " + (paper.abstract or "")
        for compound in ex.compounds:
            if not compound.strip():
                continue
            total += 1
            if _compound_appears_in_paper(compound, text):
                verified += 1
            else:
                misses.append({"pmid": ex.pmid, "compound": compound})
    precision = (verified / total) if total else 0.0
    return {
        "total_compounds": total,
        "verified_in_source": verified,
        "precision": precision,
        "misses": misses,
    }


# --- citation grounding ---------------------------------------------------

# Same pattern used by render.py for rendering inline citations.
_CITATION_RE = re.compile(r"\[\s*PMID[:\s]\s*([^\]]+?)\s*\]")
_DIGITS_RE = re.compile(r"\d+")


def evaluate_citation_grounding(state: PipelineState) -> dict[str, Any]:
    """Score (cited PMIDs that were actually retrieved) / (all cited PMIDs).

    The summary is allowed to use `[PMID:123]` tags. Each must correspond to
    a PMID we actually pulled and extracted; if it does not, the model has
    fabricated a citation.
    """
    if state.briefing is None:
        return {"total_citations": 0, "grounded": 0, "grounding": 0.0, "fabricated": []}

    summary = state.briefing.summary or ""
    retrieved_pmids = {p.pmid for p in state.papers}

    citations: list[str] = []
    for match in _CITATION_RE.finditer(summary):
        for pmid in _DIGITS_RE.findall(match.group(1)):
            citations.append(pmid)

    grounded = sum(1 for pmid in citations if pmid in retrieved_pmids)
    fabricated = [pmid for pmid in citations if pmid not in retrieved_pmids]
    grounding = (grounded / len(citations)) if citations else 0.0

    # Deduplicate fabricated for the report.
    fabricated_uniq = []
    seen: set[str] = set()
    for pmid in fabricated:
        if pmid not in seen:
            fabricated_uniq.append(pmid)
            seen.add(pmid)

    return {
        "total_citations": len(citations),
        "grounded": grounded,
        "grounding": grounding,
        "fabricated": fabricated_uniq,
    }


# --- main ----------------------------------------------------------------


async def main() -> int:
    load_dotenv()
    print(f"Folio quality eval — {len(QUERIES)} queries, {PAPERS_PER_QUERY} papers each.\n")
    rows: list[dict[str, Any]] = []
    for i, query in enumerate(QUERIES, 1):
        print(f"[{i}/{len(QUERIES)}] {query}")
        t0 = time.monotonic()
        try:
            state = await run_pipeline_to_state(query, PAPERS_PER_QUERY)
        except Exception as exc:  # noqa: BLE001
            print(f"   ERROR: {exc}\n")
            rows.append({"query": query, "error": str(exc)})
            continue
        elapsed = time.monotonic() - t0
        prec = evaluate_extraction_precision(state)
        ground = evaluate_citation_grounding(state)
        rows.append({"query": query, "elapsed": elapsed, "prec": prec, "ground": ground})
        print(
            f"   precision {prec['verified_in_source']}/{prec['total_compounds']} "
            f"({prec['precision']*100:.0f}%) · "
            f"citations grounded {ground['grounded']}/{ground['total_citations']} "
            f"({ground['grounding']*100:.0f}%) · {elapsed:.1f}s"
        )
        if prec["misses"]:
            for m in prec["misses"][:3]:
                print(f"      precision miss: PMID {m['pmid']} -> '{m['compound']}'")
        if ground["fabricated"]:
            print(f"      fabricated citations: {ground['fabricated']}")
        print()

    print("\n## Quality eval results\n")
    print(_format_markdown(rows))
    return 0


def _format_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| Query | Extraction precision | Citation grounding | Time |",
        "|---|---|---|---|",
    ]
    sum_prec_t = sum_prec_v = sum_g_t = sum_g_g = 0
    for r in rows:
        if "error" in r:
            lines.append(f"| {r['query']} | error: {r['error']} | — | — |")
            continue
        prec = r["prec"]
        g = r["ground"]
        sum_prec_t += prec["total_compounds"]
        sum_prec_v += prec["verified_in_source"]
        sum_g_t += g["total_citations"]
        sum_g_g += g["grounded"]
        prec_cell = (
            f"{prec['verified_in_source']}/{prec['total_compounds']} "
            f"({prec['precision']*100:.0f}%)"
        )
        g_cell = (
            f"{g['grounded']}/{g['total_citations']} "
            f"({g['grounding']*100:.0f}%)"
            if g["total_citations"]
            else "—"
        )
        lines.append(f"| {r['query']} | {prec_cell} | {g_cell} | {r['elapsed']:.1f}s |")
    overall_prec = f"{sum_prec_v}/{sum_prec_t} ({sum_prec_v/sum_prec_t*100:.0f}%)" if sum_prec_t else "—"
    overall_ground = f"{sum_g_g}/{sum_g_t} ({sum_g_g/sum_g_t*100:.0f}%)" if sum_g_t else "—"
    lines.append(f"| **Overall** | **{overall_prec}** | **{overall_ground}** | |")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
