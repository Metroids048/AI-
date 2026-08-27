"""Contracts for turning video knowledge into falsifiable quant primitives.

The contracts are research-only.  They deliberately preserve provenance and
make proxy quantization explicit so a derived rule cannot be mistaken for the
speaker's original trading system.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from services.research.integrations.contracts import ResearchExperimentSpec, stable_hash
from shared.models import PlatformModel

PrimitiveRole = Literal[
    "EVENT",
    "FILTER",
    "REGIME",
    "LEVEL",
    "CONFIRMATION",
    "VETO",
    "EXIT_HYPOTHESIS",
    "PARAMETER_PRIOR",
]
SourceProvenance = Literal[
    "SOURCE_EXACT",
    "PROXY_DERIVED",
    "CORPUS_INSPIRED",
    "DISCRETIONARY_ONLY",
    "CONFLICTED",
]
QuantizationStatus = Literal["SOURCE_EXACT", "PROXY_ALLOWED", "DISCRETIONARY_ONLY", "CONFLICTED"]
LookaheadRisk = Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]


class QuantPrimitive(PlatformModel):
    primitive_id: str
    concept: str
    role: PrimitiveRole
    source_support_count: int = Field(default=0, ge=0)
    source_videos: list[str] = Field(default_factory=list)
    source_units: list[str] = Field(default_factory=list)
    natural_language_thesis: str
    quantization_status: QuantizationStatus
    quantization: dict[str, Any] = Field(default_factory=dict)
    required_features: list[str] = Field(default_factory=list)
    parameter_priors: dict[str, Any] = Field(default_factory=dict)
    lookahead_risk: LookaheadRisk = "UNKNOWN"
    contradictions: list[str] = Field(default_factory=list)
    provenance: SourceProvenance
    applicable_timeframes: list[str] = Field(default_factory=list)
    applicable_regimes: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)

    @field_validator("source_support_count")
    @classmethod
    def _support_is_consistent(cls, value: int) -> int:
        return value

    @property
    def primitive_hash(self) -> str:
        return stable_hash(self.model_dump(mode="json"))


class ResearchHypothesis(PlatformModel):
    research_design_version: int = 2
    hypothesis_id: str
    claim: str
    base_event: str
    primitive: str
    experiment_type: str = "ATOMIC_EDGE"
    parent_universe: dict[str, Any] = Field(default_factory=dict)
    baseline_selector: dict[str, Any] = Field(default_factory=dict)
    candidate_selector: dict[str, Any] = Field(default_factory=dict)
    parameter_space: dict[str, Any] = Field(default_factory=dict)
    feature_formula_hash: str = ""
    metric: Literal["forward_return", "mfe", "mae", "hit_rate", "net_return", "net_expectancy"]
    horizons: list[int] = Field(default_factory=lambda: [4, 8, 16])
    split_plan: dict[str, Any] = Field(default_factory=dict)
    cost_model: dict[str, Any] = Field(default_factory=dict)
    symbols: list[str] = Field(default_factory=list)
    timeframes: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    registered_before_evaluation: bool = False

    @property
    def hypothesis_hash(self) -> str:
        return stable_hash(self.model_dump(mode="json"))

    def to_experiment_spec(
        self,
        *,
        dataset_id: str,
        dataset_hash: str,
        strategy_hash: str,
        parameter_space: dict[str, Any] | None = None,
        symbols: list[str] | None = None,
        timeframes: list[str] | None = None,
    ) -> ResearchExperimentSpec:
        """Bind a registered hypothesis to the existing engine-neutral spec."""
        if not self.registered_before_evaluation:
            raise ValueError("HYPOTHESIS_MUST_BE_REGISTERED_BEFORE_EVALUATION")
        return ResearchExperimentSpec(
            strategy_id=f"hypothesis:{self.hypothesis_id}",
            strategy_version=self.hypothesis_hash[:12],
            strategy_hash=strategy_hash,
            dataset_id=dataset_id,
            dataset_hash=dataset_hash,
            symbols=symbols or [],
            timeframes=timeframes or [],
            split_plan=self.split_plan,
            cost_model=self.cost_model,
            parameter_space=parameter_space or {},
            engine_options={
                "research_hypothesis_id": self.hypothesis_id,
                "research_hypothesis_hash": self.hypothesis_hash,
                "base_event": self.base_event,
                "primitive_id": self.primitive,
                "metric": self.metric,
                "horizons": self.horizons,
                "research_design_version": self.research_design_version,
                "experiment_type": self.experiment_type,
                "parent_universe": self.parent_universe,
                "baseline_selector": self.baseline_selector,
                "candidate_selector": self.candidate_selector,
                "parameter_space": self.parameter_space,
                "feature_formula_hash": self.feature_formula_hash,
            },
        )


class QuantKnowledgeBundle(PlatformModel):
    schema_version: str = "1.0"
    corpus_id: str
    generated_at: str
    source_manifest: dict[str, Any] = Field(default_factory=dict)
    primitives: list[QuantPrimitive] = Field(default_factory=list)
    hypotheses: list[ResearchHypothesis] = Field(default_factory=list)
    quantization_proposals: list[dict[str, Any]] = Field(default_factory=list)
    export_hash: str | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.export_hash is None:
            payload = self.model_dump(mode="json", exclude={"export_hash"})
            payload.pop("generated_at", None)
            source_manifest = payload.get("source_manifest")
            if isinstance(source_manifest, dict):
                source_manifest = dict(source_manifest)
                for key in ("generated_at", "corpus_root", "agent_corpus_path", "rule_candidates_path"):
                    source_manifest.pop(key, None)
                payload["source_manifest"] = source_manifest
            object.__setattr__(self, "export_hash", stable_hash(payload))


__all__ = [
    "LookaheadRisk",
    "PrimitiveRole",
    "QuantKnowledgeBundle",
    "QuantPrimitive",
    "QuantizationStatus",
    "ResearchHypothesis",
    "SourceProvenance",
]
