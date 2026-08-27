# Video Quant Knowledge Export

## Purpose

The 273-video corpus is an **Alpha Hypothesis Source**, not a requirement to
reconstruct a speaker's complete mechanical strategy. The export boundary turns
clean knowledge units into research-only quantitative primitives and hypotheses.

## Flow

```text
video Clean Corpus -> Agent Corpus / Knowledge Units
                   -> Quant Knowledge Export
                   -> Primitive Registry + Hypothesis Registry
                   -> paired Event/Filter ablation
                   -> existing ResearchExperimentSpec / Orchestrator
                   -> V2 terminal classification / OOS closeout
```

The exporter writes `quant_knowledge_bundle.jsonl`, `quant_concepts.jsonl`,
`quantization_proposals.jsonl`, and `source_manifest.json` under
`artifacts/strategy_research/export/` by default.

## Provenance and safety

- `SOURCE_EXACT` is reserved for an explicitly stated, mechanically complete rule.
- `PROXY_DERIVED` means the platform supplied a measurable proxy and does not claim
  fidelity to the speaker's original rule.
- `CORPUS_INSPIRED` records a research idea that is not a direct quantization.
- `DISCRETIONARY_ONLY` and `CONFLICTED` remain non-executable.
- Every exported proposal is `research_only`; no production manifest, execution
  authority, order, position, or risk setting is modified.
- Hypotheses must be registered before they can be bound to a
  `ResearchExperimentSpec`, preserving pre-result claim semantics.

## Research Design V2

V2 hashes the complete experiment contract: parent universe, baseline and candidate
selectors, experiment type, explicit parameter space, feature-formula hash, horizons,
symbols, timeframes, cost model, and split plan. A candidate already implied by
EventEdge admission is recorded as `TAUTOLOGY_FAIL`, not as negative alpha. Filter,
regime, confirmation, veto, and level comparisons use paired bootstrap by resampling
the parent event universe and then reapplying the predicate.

V1 artifacts remain append-only evidence and are referenced as
`SUPERSEDED_EXPERIMENT_DESIGN` when V2 invalidates their interpretation. Final Holdout
is never opened when no eligible candidate exists; the terminal state is
`SEALED_NO_ELIGIBLE_CANDIDATE`.

## Search-width guard

`compose_research_candidate` permits at most one `FILTER` and one
`CONFIRMATION` on a base event. This keeps first-stage comparisons paired and
prevents the video corpus from becoming an unbounded hyperparameter search.
