from __future__ import annotations

import functools

import pytest


@pytest.fixture(autouse=True)
def _isolate_asset_root(tmp_path, monkeypatch):
    """Keep asset ingestion out of the working tree.

    ``OpenSourceStrategyLibrary`` defaults ``asset_root`` to the real
    ``research_source/open_source_strategy_library/assets`` directory, so these
    tests used to rewrite the tracked ``asset_manifest.json`` (new timestamps)
    on every run. That left ``git status`` dirty after a plain ``pytest`` and
    collides with pre-commit's stash/restore cycle once the hook runs tests.

    Both construction sites resolve the class from their own module globals, so
    patching the symbol in each is enough to redirect writes to tmp_path.
    """

    from apps.api.routers import research_sources as research_sources_router
    from research_source.open_source_strategy_library import OpenSourceStrategyLibrary
    from services.agents import service as agents_service

    isolated = functools.partial(OpenSourceStrategyLibrary, asset_root=tmp_path / "assets")
    monkeypatch.setattr(research_sources_router, "OpenSourceStrategyLibrary", isolated)
    monkeypatch.setattr(agents_service, "OpenSourceStrategyLibrary", isolated)


def test_research_source_api_imports_and_extracts_strategy_ideas(api_client) -> None:
    list_resp = api_client.get("/api/v1/research-sources")
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] >= 12

    import_resp = api_client.post(
        "/api/v1/research-sources/import",
        json={"source_ids": ["freqtrade"], "refresh_assets": True, "fetch_remote": False},
    )
    assert import_resp.status_code == 202
    assert import_resp.json()["imported"][0]["source_id"] == "freqtrade"
    assert import_resp.json()["imported_assets"]

    assets_resp = api_client.get("/api/v1/research-sources/freqtrade/assets")
    assert assets_resp.status_code == 200
    assert assets_resp.json()["total"] >= 1

    extract_resp = api_client.post(
        "/api/v1/research-sources/freqtrade/extract-ideas",
        json={"persist_ideas": True, "max_ideas": 3},
    )
    assert extract_resp.status_code == 200
    body = extract_resp.json()
    assert body["total"] >= 2
    assert all(item["source"].startswith("open_source:freqtrade") for item in body["items"])


def test_research_source_asset_endpoints_return_404_for_unknown_source(api_client) -> None:
    assets_resp = api_client.get("/api/v1/research-sources/not-real/assets")
    refresh_resp = api_client.post("/api/v1/research-sources/not-real/refresh-assets")

    assert assets_resp.status_code == 404
    assert refresh_resp.status_code == 404


def test_research_runtime_reports_pins_and_keeps_production_pending(api_client) -> None:
    response = api_client.get("/api/v1/research-sources/runtime")
    assert response.status_code == 200
    body = response.json()
    assert body["research_only"] is True
    assert body["production_authorization"] == "PENDING"
    assert body["pinned_sources"]["engines"]["vectorbt"]["license"] == "Apache-2.0 + Commons Clause"
    assert body["pinned_sources"]["engines"]["freqtrade"]["integration_mode"] == "external_subprocess_validator"


def test_agent_tasks_import_extract_and_materialize_open_source_drafts(api_client) -> None:
    import_task = api_client.post(
        "/api/v1/agents/tasks",
        json={
            "agent_type": "research_agent",
            "task_type": "import_open_source_sources",
            "input_payload": {"source_ids": ["freqtrade"], "refresh_assets": True, "fetch_remote": False},
        },
    )
    assert import_task.status_code == 202
    import_task_id = import_task.json()["resource_id"]
    import_result = api_client.get(f"/api/v1/agents/tasks/{import_task_id}").json()
    assert import_result["task_status"] == "completed"
    assert import_result["output_payload"]["imported_count"] == 1
    assert import_result["output_payload"]["asset_count"] >= 1

    extract_task = api_client.post(
        "/api/v1/agents/tasks",
        json={
            "agent_type": "research_agent",
            "task_type": "extract_open_source_strategy_ideas",
            "input_payload": {"source_ids": ["freqtrade"], "persist_ideas": True},
        },
    )
    assert extract_task.status_code == 202
    extract_task_id = extract_task.json()["resource_id"]
    extract_result = api_client.get(f"/api/v1/agents/tasks/{extract_task_id}").json()
    assert extract_result["task_status"] == "completed"
    assert extract_result["output_payload"]["idea_count"] >= 2

    draft_task = api_client.post(
        "/api/v1/agents/tasks",
        json={
            "agent_type": "strategy_agent",
            "task_type": "materialize_seed_strategy_drafts",
            "input_payload": {},
        },
    )
    assert draft_task.status_code == 202
    draft_task_id = draft_task.json()["resource_id"]
    draft_result = api_client.get(f"/api/v1/agents/tasks/{draft_task_id}").json()
    assert draft_result["task_status"] == "completed"
    assert draft_result["output_payload"]["draft_count"] >= 2

    drafts = api_client.get("/api/v1/strategies/drafts").json()["items"]
    assert all(draft["rules"]["stoploss_rules"] for draft in drafts)
    assert all(draft["rules"]["position_rules"] for draft in drafts)
