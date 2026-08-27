"""In-memory registries for primitives and pre-registered hypotheses."""

from __future__ import annotations

from collections.abc import Iterable

from .contracts import QuantPrimitive, ResearchHypothesis


class QuantPrimitiveRegistry:
    def __init__(self, primitives: Iterable[QuantPrimitive] = ()) -> None:
        self._items: dict[str, QuantPrimitive] = {}
        for primitive in primitives:
            self.register(primitive)

    def register(self, primitive: QuantPrimitive) -> QuantPrimitive:
        existing = self._items.get(primitive.primitive_id)
        if existing is not None and existing.primitive_hash != primitive.primitive_hash:
            raise ValueError(f"PRIMITIVE_ID_CONFLICT: {primitive.primitive_id}")
        self._items[primitive.primitive_id] = primitive
        return primitive

    def get(self, primitive_id: str) -> QuantPrimitive | None:
        return self._items.get(primitive_id)

    def list(self, *, role: str | None = None) -> list[QuantPrimitive]:
        items: Iterable[QuantPrimitive] = self._items.values()
        if role is not None:
            items = (item for item in items if item.role == role)
        return sorted(items, key=lambda item: item.primitive_id)

    def __len__(self) -> int:
        return len(self._items)


class HypothesisRegistry:
    """Prevents post-result reinterpretation by requiring explicit registration."""

    def __init__(self, hypotheses: Iterable[ResearchHypothesis] = ()) -> None:
        self._items: dict[str, ResearchHypothesis] = {}
        self._evaluated: set[str] = set()
        for hypothesis in hypotheses:
            self.register(hypothesis)

    def register(self, hypothesis: ResearchHypothesis) -> ResearchHypothesis:
        if hypothesis.hypothesis_id in self._evaluated:
            raise ValueError(f"HYPOTHESIS_ALREADY_EVALUATED: {hypothesis.hypothesis_id}")
        existing = self._items.get(hypothesis.hypothesis_id)
        if existing is not None and existing.hypothesis_hash != hypothesis.hypothesis_hash:
            raise ValueError(f"HYPOTHESIS_ID_CONFLICT: {hypothesis.hypothesis_id}")
        if not hypothesis.registered_before_evaluation:
            hypothesis = hypothesis.model_copy(update={"registered_before_evaluation": True})
        self._items[hypothesis.hypothesis_id] = hypothesis
        return hypothesis

    def get(self, hypothesis_id: str) -> ResearchHypothesis | None:
        return self._items.get(hypothesis_id)

    def mark_evaluated(self, hypothesis_id: str) -> None:
        if hypothesis_id not in self._items:
            raise KeyError(f"HYPOTHESIS_NOT_REGISTERED: {hypothesis_id}")
        self._evaluated.add(hypothesis_id)

    def list(self) -> list[ResearchHypothesis]:
        return sorted(self._items.values(), key=lambda item: item.hypothesis_id)


__all__ = ["HypothesisRegistry", "QuantPrimitiveRegistry"]
