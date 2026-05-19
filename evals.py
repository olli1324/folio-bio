"""Mini benchmark for Folio.

Runs five canonical drug-discovery queries through the pipeline and scores
whether the briefing's `top_compounds` recovers the compounds a domain expert
would expect to see for that target / disease. Prints a Markdown table.

The expected lists were curated from the standard-of-care literature for each
indication; aliases (development code names, antibody-drug-conjugate
shorthands) are grouped per molecule and a hit on any alias counts once.

Run from the repo root:

    python evals.py

The pipeline already drives Featherless via FEATHERLESS_API_KEY in .env; the
benchmark needs no extra configuration beyond the standard pipeline setup.
"""

from __future__ import annotations

import asyncio
import time
from typing import Iterable

from dotenv import load_dotenv

from engine.pipeline import run_pipeline
from engine.schema import Stage

# Each entry: (query, list of compounds we expect to see in top_compounds).
# Compounds are matched case-insensitively as substrings either way. Aliases
# for the same molecule are grouped in a tuple so a hit on any one counts.
QUERIES: list[tuple[str, list]] = [
    (
        "EGFR inhibitors for non-small-cell lung cancer",
        [
            ("osimertinib", "AZD9291"),
            "erlotinib",
            "gefitinib",
            "afatinib",
            "dacomitinib",
        ],
    ),
    (
        "BRAF V600E melanoma",
        [
            "dabrafenib",
            "trametinib",
            "vemurafenib",
            "encorafenib",
            "binimetinib",
        ],
    ),
    (
        "KRAS G12C inhibitors for solid tumors",
        [
            "sotorasib",
            "adagrasib",
            "divarasib",
        ],
    ),
    (
        "HER2 positive breast cancer",
        [
            "trastuzumab",
            "pertuzumab",
            ("trastuzumab emtansine", "T-DM1", "ado-trastuzumab"),
            ("trastuzumab deruxtecan", "T-DXd"),
            "tucatinib",
            "neratinib",
            "lapatinib",
        ],
    ),
    (
        "PD-L1 checkpoint inhibitors for cancer",
        [
            "atezolizumab",
            "durvalumab",
            "avelumab",
        ],
    ),
]


def _normalize(name: str) -> str:
    return name.lower().strip().replace("-", " ")


def _aliases(expected: object) -> tuple[str, ...]:
    if isinstance(expected, str):
        return (expected,)
    return tuple(expected)  # type: ignore[arg-type]


def _matched_alias(expected: object, found_names: Iterable[str]) -> str | None:
    """Return which alias matched any of `found_names`, or None."""
    normalized = [_normalize(n) for n in found_names]
    for alias in _aliases(expected):
        a = _normalize(alias)
        if not a:
            continue
        for f in normalized:
            if a in f or f in a:
                return alias
    return None


async def run_one(query: str, expected: list) -> dict:
    """Run one query through the pipeline and score it."""
    t0 = time.monotonic()
    briefing: dict | None = None
    error: str | None = None
    async for event in run_pipeline(query):
        if event.stage is Stage.DONE:
            briefing = event.detail.get("briefing")
        elif event.stage is Stage.ERROR:
            error = event.message
    elapsed = time.monotonic() - t0

    if error or briefing is None:
        return {
            "query": query,
            "error": error or "no briefing returned",
            "elapsed": elapsed,
        }

    found_names = [c.get("name", "") for c in briefing.get("top_compounds", [])]
    hits: list[str] = []
    misses: list[str] = []
    for exp in expected:
        matched = _matched_alias(exp, found_names)
        if matched:
            hits.append(matched)
        else:
            misses.append(_aliases(exp)[0])

    return {
        "query": query,
        "found": found_names,
        "hits": hits,
        "misses": misses,
        "n_expected": len(expected),
        "elapsed": elapsed,
    }


def format_markdown(results: list[dict]) -> str:
    """Render scoring results as a Markdown table."""
    lines = [
        "| Query | Recall | Found | Missing | Time |",
        "|---|---|---|---|---|",
    ]
    total_hits = 0
    total_expected = 0
    for r in results:
        if "error" in r:
            lines.append(
                f"| {r['query']} | error | | {r['error']} | {r['elapsed']:.1f}s |"
            )
            continue
        n_hits = len(r["hits"])
        n_exp = r["n_expected"]
        total_hits += n_hits
        total_expected += n_exp
        pct = (n_hits / n_exp * 100) if n_exp else 0.0
        recall = f"{n_hits}/{n_exp} ({pct:.0f}%)"
        found = ", ".join(r["hits"]) or "--"
        miss = ", ".join(r["misses"]) or "--"
        lines.append(
            f"| {r['query']} | {recall} | {found} | {miss} | {r['elapsed']:.1f}s |"
        )
    if total_expected:
        overall_pct = total_hits / total_expected * 100
        overall = f"{total_hits}/{total_expected} ({overall_pct:.0f}%)"
    else:
        overall = "--"
    lines.append(f"| **Overall** | **{overall}** | | | |")
    return "\n".join(lines)


async def main() -> int:
    load_dotenv()
    print(f"Running {len(QUERIES)} benchmark queries (sequential, ~30s each)...\n")
    results: list[dict] = []
    for i, (query, expected) in enumerate(QUERIES, 1):
        print(f"[{i}/{len(QUERIES)}] {query}")
        r = await run_one(query, expected)
        results.append(r)
        if "error" in r:
            print(f"   ERROR: {r['error']} ({r['elapsed']:.1f}s)\n")
        else:
            print(
                f"   recall {len(r['hits'])}/{r['n_expected']}"
                f" in {r['elapsed']:.1f}s"
                f" (found: {', '.join(r['hits']) or '--'})\n"
            )

    print("\n## Benchmark results\n")
    print(format_markdown(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
