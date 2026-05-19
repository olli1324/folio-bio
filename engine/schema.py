"""Data models for the Folio research engine.

Everything that flows through the pipeline is defined here. `PipelineState` is
the single object each node reads and returns; the rest are the structured
pieces that get built up along the way.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Stage(str, Enum):
    """Pipeline stages, also used as progress-event labels."""

    PLAN = "plan"
    SEARCH = "search"
    RETRIEVE = "retrieve"
    EXTRACT = "extract"
    SYNTHESISE = "synthesise"
    ENRICH = "enrich"
    FORMAT = "format"
    DONE = "done"
    ERROR = "error"


class ProgressEvent(BaseModel):
    """Emitted by `run_pipeline` as each stage progresses.

    The web layer turns these into SSE messages; the CLI prints them. `detail`
    carries structured payloads (e.g. the finished briefing on the DONE event).
    """

    stage: Stage
    message: str
    detail: dict = Field(default_factory=dict)


class Paper(BaseModel):
    """A single PubMed record."""

    pmid: str
    title: str
    abstract: str = ""
    authors: list[str] = Field(default_factory=list)
    journal: str = ""
    year: str = ""

    @property
    def url(self) -> str:
        return f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/"

    @property
    def citation(self) -> str:
        lead = self.authors[0] if self.authors else "Unknown"
        suffix = " et al." if len(self.authors) > 1 else ""
        return f"{lead}{suffix} ({self.year}). {self.title}. {self.journal}."


class Extraction(BaseModel):
    """What the extraction node pulls from one paper.

    Beyond the basic compound/mechanism/finding fields, this carries the
    specifics a medicinal chemist actually triages on: which variant or
    mutation was tested, any reported potency / selectivity numbers, whether
    the study is preclinical or clinical, and any resistance mechanism
    discussed. All four fields are free-text and may be empty when the
    abstract does not mention them.
    """

    pmid: str
    compounds: list[str] = Field(default_factory=list)
    mechanism_of_action: str = ""
    key_finding: str = ""
    # 0..1 -- how relevant this paper is to the query, judged by the model.
    relevance_score: float = 0.0

    # Medicinal-chemistry fields. Empty string when the abstract is silent.
    variant_or_mutation: str = ""
    potency: str = ""
    selectivity: str = ""
    # Free text but expected to be one of: preclinical / clinical / review /
    # real-world / unknown. Validated lightly downstream.
    study_type: str = ""
    resistance_mechanism: str = ""


class TrialInfo(BaseModel):
    """ClinicalTrials.gov enrichment for one compound.

    Aggregates the most useful at-a-glance signal: how far the compound has
    progressed in trials, how much current vs historical activity exists, and
    a few NCT IDs the reader can drill into.
    """

    # Highest phase reached among interventional trials, e.g. "PHASE3".
    # Empty string if no phased interventional trials were found.
    highest_phase: str = ""
    # Counts across interventional trials matching the compound and query.
    active_trials: int = 0
    completed_trials: int = 0
    total_trials: int = 0
    # Up to 3 representative NCT IDs, most recently updated first.
    sample_ncts: list[str] = Field(default_factory=list)


class CompoundCandidate(BaseModel):
    """A compound surfaced by the synthesis node, with its supporting papers."""

    name: str
    rationale: str = ""
    supporting_pmids: list[str] = Field(default_factory=list)
    trial_info: TrialInfo | None = None


class Briefing(BaseModel):
    """The final structured deliverable."""

    query: str
    summary: str = ""
    top_compounds: list[CompoundCandidate] = Field(default_factory=list)
    mechanisms: list[str] = Field(default_factory=list)
    key_papers: list[Paper] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class PipelineState(BaseModel):
    """The object every node reads and returns.

    Keeping all state in one typed object means the nodes stay pure-ish and
    port cleanly to LangGraph nodes later if the team wants the graph library.
    """

    query: str
    search_terms: list[str] = Field(default_factory=list)
    pmids: list[str] = Field(default_factory=list)
    papers: list[Paper] = Field(default_factory=list)
    extractions: list[Extraction] = Field(default_factory=list)
    briefing: Briefing | None = None
    briefing_markdown: str | None = None
    error: str | None = None
