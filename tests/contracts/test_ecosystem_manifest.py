from __future__ import annotations

import json
from pathlib import Path


def test_ecosystem_research_sources_have_explicit_license_boundaries() -> None:
    path = Path("research_source/open_source_strategy_library/manifests/seed_sources.json")
    sources = {item["source_id"]: item for item in json.loads(path.read_text(encoding="utf-8"))}

    expected = {
        "superalgos": ("Apache-2.0", "distilled_research_allowed"),
        "jesse": ("MIT", "distilled_research_allowed"),
        "nautilus_trader": ("LGPL-3.0", "distilled_research_only"),
        "qlib": ("MIT", "distilled_research_allowed"),
        "vectorbt": ("Apache-2.0 + Commons Clause", "distilled_research_allowed"),
        "openbb": ("AGPL-3.0", "distilled_research_only"),
        "lumen": ("MIT", "distilled_research_allowed"),
        "hydraquant": ("GPL-3.0", "distilled_research_only"),
        "basis_funding_arbitrage_bot": ("MIT", "distilled_research_allowed"),
        "hedge_fund_committee": ("MIT", "distilled_research_allowed"),
        "riverflow_apex": ("MIT", "distilled_research_allowed"),
    }
    for source_id, boundary in expected.items():
        assert (sources[source_id]["license"], sources[source_id]["license_policy"]) == boundary
        assert sources[source_id]["asset_allowlist"]
        assert sources[source_id]["license_notes"]
