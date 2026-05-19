"""Prompts for the three model-driven nodes.

Kept in one file so they are easy to tune without touching pipeline logic.
Every prompt asks for strict JSON; the Featherless client parses it leniently
(see `featherless.chat_json`).
"""

from __future__ import annotations

from engine.schema import Extraction, Paper

# --- plan ------------------------------------------------------------------

_PLAN_SYSTEM = (
    "You are a biomedical search strategist. Given a researcher's target "
    "protein or disease area, produce a focused set of PubMed search terms. "
    "Cover up to three classes when relevant, with one or two terms each: "
    "(1) target / gene synonyms (HGNC symbol plus common aliases), "
    "(2) the disease or indication (and its specific subtype if named), "
    "(3) drug class or representative drug names. "
    "Return 4 to 8 terms total, deduplicated -- do not repeat the same concept "
    "in different surface forms. Favour precision over breadth. Respond ONLY "
    'with JSON of the form {"search_terms": ["term one", "term two", ...]}.'
)


def plan_messages(query: str) -> list[dict]:
    return [
        {"role": "system", "content": _PLAN_SYSTEM},
        {"role": "user", "content": f"Target / area: {query}"},
    ]


# --- extract ---------------------------------------------------------------

_EXTRACT_SYSTEM = (
    "You are a drug-discovery analyst reading one paper at a time. From the "
    "title and abstract, extract these fields:\n"
    " - compounds: a list of specific named drugs or molecules whose proper "
    "name (generic name or development code, e.g. osimertinib, AZD9291, "
    "T-DM1, sotorasib, trastuzumab) appears verbatim in the title or "
    "abstract. Apply these rules strictly:\n"
    "     * Only list a compound that is actually named in the source text. "
    "Do NOT infer specific drugs from a drug-class mention. If the abstract "
    "refers only to a class (e.g. \"PD-(L)1 blockade\", \"EGFR-TKIs\", "
    "\"checkpoint inhibitors\", \"HER2-targeted therapy\") without naming "
    "specific molecules, return an empty list.\n"
    "     * Never list a drug class as a compound. Phrases like \"PD-1 "
    "inhibitor\", \"PD-L1 inhibitors\", \"EGFR-TKI\", \"anti-PD-1\", "
    "\"anti-PD-L1\", \"anti-HER2\", \"checkpoint inhibitor\", \"HER2-"
    "targeted therapy\", \"KRAS G12C inhibitor\" are CLASSES, not compounds.\n"
    "     * Combinations: split \"drug A + drug B\" into separate items.\n"
    " - mechanism_of_action: one short clause describing the mechanism the "
    "paper studies.\n"
    " - key_finding: one sentence stating the single most important, "
    "actionable result reported.\n"
    " - variant_or_mutation: the specific variant, mutation or allele tested "
    "(e.g. \"T790M\", \"C797S\", \"G12C\", \"V600E\", \"exon 20 insertion\"). "
    "Empty string if none specified.\n"
    " - potency: any reported potency value, exactly as written "
    "(e.g. \"IC50 1.6 nM\", \"EC50 12 nM\", \"Ki 0.3 nM\"). Empty if none.\n"
    " - selectivity: short selectivity note if reported (e.g. \"wild-type "
    "EGFR sparing\", \"100-fold vs HER2\", \"CNS-penetrant\"). Empty if none.\n"
    " - study_type: one of preclinical / clinical / review / real-world / "
    "unknown. Pick the best fit; use \"unknown\" only if truly unclear.\n"
    " - resistance_mechanism: any resistance pathway discussed (e.g. \"T790M "
    "secondary mutation\", \"MET amplification\", \"BRAF amplification\"). "
    "Empty if not discussed.\n"
    " - relevance_score: a number from 0 to 1 scored against the stated "
    "research query using this rubric:\n"
    "     0.9-1.0  directly addresses the query (the target/compound is "
    "studied in this disease)\n"
    "     0.6-0.8  closely related (same target, different disease, or vice "
    "versa)\n"
    "     0.3-0.5  tangential (general review, mechanism context, off-target)\n"
    "     0.0-0.2  unrelated\n"
    "Leave any field empty when the abstract does not mention it. Do not "
    "invent compounds, potencies, or mutations. Respond ONLY with JSON of "
    'the form {"compounds": [...], "mechanism_of_action": "...", '
    '"key_finding": "...", "variant_or_mutation": "...", "potency": "...", '
    '"selectivity": "...", "study_type": "...", '
    '"resistance_mechanism": "...", "relevance_score": 0.0}.'
)


def extract_messages(query: str, paper: Paper) -> list[dict]:
    body = (
        f"Research query: {query}\n\n"
        f"Title: {paper.title}\n"
        f"Journal/Year: {paper.journal} {paper.year}\n"
        f"Abstract: {paper.abstract or '(no abstract available)'}"
    )
    return [
        {"role": "system", "content": _EXTRACT_SYSTEM},
        {"role": "user", "content": body},
    ]


# --- synthesise ------------------------------------------------------------

_SYNTHESIS_SYSTEM = (
    "You are a senior drug-discovery researcher writing a briefing for your "
    "team. You are given per-paper extractions for a research query. Produce "
    "four sections, using only compounds, mechanisms and findings present in "
    "the extractions:\n"
    " - summary: 3 to 5 sentences on the state of the literature for THIS "
    "specific query. Cite the supporting extractions inline using square-"
    "bracket PMID tags placed immediately after the claim they support, "
    "for example: [PMID:12345678]. Stack multiple PMIDs as "
    "[PMID:12345678,87654321]. Use only PMIDs that appear in the extractions "
    "given below.\n"
    " - top_compounds: at most 5 named molecules, ranked by combined evidence "
    "strength (count and relevance of supporting extractions) and how directly "
    "they address the query. Each entry has a one-sentence rationale and the "
    "PMIDs that support it. Prefer the approved generic name; treat "
    "development code names as aliases of the same compound (e.g. AZD9291 is "
    "osimertinib, LY2157299 is galunisertib) and report each molecule only "
    "once. For any compound supported by only one paper, prefix the rationale "
    "with 'Early signal: '.\n"
    " - mechanisms: deduplicated mechanisms of action that recur AND are "
    "relevant to the query target or disease. Exclude generic chemotherapy, "
    "delivery-platform or adjacent-pathway mechanisms unless the query is "
    "about those.\n"
    " - open_questions: 3 to 5 specific gaps the literature does not resolve. "
    "If two or more extractions clearly disagree, add an item starting with "
    "'Conflicting evidence:' that names the disagreement.\n"
    "Respond ONLY with JSON of the form "
    '{"summary": "...", "top_compounds": [{"name": "...", "rationale": "...", '
    '"supporting_pmids": ["..."]}], "mechanisms": ["..."], '
    '"open_questions": ["..."]}.'
)


def synthesis_messages(query: str, extractions: list[Extraction]) -> list[dict]:
    lines = []
    for e in extractions:
        parts = [
            f"PMID {e.pmid} (relevance {e.relevance_score:.2f}, "
            f"{e.study_type or 'unknown'})",
            f"  compounds: {', '.join(e.compounds) or '-'}",
            f"  mechanism: {e.mechanism_of_action or '-'}",
            f"  finding: {e.key_finding or '-'}",
        ]
        if e.variant_or_mutation:
            parts.append(f"  variant: {e.variant_or_mutation}")
        if e.potency:
            parts.append(f"  potency: {e.potency}")
        if e.selectivity:
            parts.append(f"  selectivity: {e.selectivity}")
        if e.resistance_mechanism:
            parts.append(f"  resistance: {e.resistance_mechanism}")
        lines.append("\n".join(parts))
    body = f"Research query: {query}\n\nExtractions:\n" + "\n\n".join(lines)
    return [
        {"role": "system", "content": _SYNTHESIS_SYSTEM},
        {"role": "user", "content": body},
    ]
