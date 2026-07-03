"""Shared, layer-neutral package.

Holds the unified data contracts that every `services/*` module and the
`apps/api` layer import. Per AGENTS.md 原则二「数据契约优先」: define the
Pydantic model here BEFORE writing any collector / runner / agent code.
Nothing in this package may import from `services/` or `apps/` (no cycles).
"""
