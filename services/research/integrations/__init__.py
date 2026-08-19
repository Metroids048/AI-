"""Canonical contracts and research-engine adapters."""

from .contracts import ResearchExperimentResult, ResearchExperimentSpec
from .freqtrade_adapter import FreqtradeValidationAdapter
from .orchestrator import ResearchOrchestrator
from .research_context import ResearchContextBundle, ResearchContextItem
from .research_council import ResearchCouncil, ResearchCouncilVerdict
from .vectorbt_adapter import VectorbtScreenAdapter

__all__ = [
    "FreqtradeValidationAdapter",
    "ResearchContextBundle",
    "ResearchContextItem",
    "ResearchCouncil",
    "ResearchCouncilVerdict",
    "ResearchExperimentResult",
    "ResearchExperimentSpec",
    "ResearchOrchestrator",
    "VectorbtScreenAdapter",
]
