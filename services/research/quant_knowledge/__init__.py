"""Video-derived quantitative research knowledge, isolated from execution."""

from .ablation import AblationComparison, compare_paired_events, compose_research_candidate
from .contracts import (
    QuantKnowledgeBundle,
    QuantPrimitive,
    ResearchHypothesis,
)
from .exporter import export_quant_knowledge
from .registry import HypothesisRegistry, QuantPrimitiveRegistry

__all__ = [
    "HypothesisRegistry",
    "AblationComparison",
    "QuantKnowledgeBundle",
    "QuantPrimitive",
    "QuantPrimitiveRegistry",
    "ResearchHypothesis",
    "compare_paired_events",
    "compose_research_candidate",
    "export_quant_knowledge",
]
