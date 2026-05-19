"""Folio research engine.

The Featherless-powered literature pipeline. Import `run_pipeline` to drive it.
"""

from engine.pipeline import run_pipeline
from engine.schema import (
    Briefing,
    CompoundCandidate,
    Extraction,
    Paper,
    PipelineState,
    ProgressEvent,
    Stage,
)

__all__ = [
    "run_pipeline",
    "Briefing",
    "CompoundCandidate",
    "Extraction",
    "Paper",
    "PipelineState",
    "ProgressEvent",
    "Stage",
]
