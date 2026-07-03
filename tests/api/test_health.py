"""Smoke tests for the versioned API surface."""

from __future__ import annotations


def test_health(api_client) -> None:
    resp = api_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    v1_resp = api_client.get("/api/v1/health")
    assert v1_resp.status_code == 200
    assert v1_resp.json()["status"] == "ok"


def test_strategies_crud_seam(api_client) -> None:
    created = api_client.post(
        "/api/v1/strategies",
        json={"strategy_key": "BTC_Test_v1", "source": "manual", "core_thesis": "t"},
    )
    assert created.status_code == 201
    sid = created.json()["strategy_id"]

    assert api_client.get(f"/api/v1/strategies/{sid}").status_code == 200

    listing = api_client.get("/api/v1/strategies")
    assert listing.status_code == 200
    assert any(s["strategy_id"] == sid for s in listing.json()["items"])

    assert api_client.delete(f"/api/v1/strategies/{sid}").status_code == 204
    assert api_client.get(f"/api/v1/strategies/{sid}").status_code == 404


def test_interface_cluster_skeleton_routes(api_client) -> None:
    for path in [
        "/api/v1/strategies/ideas",
        "/api/v1/strategies/drafts",
        "/api/v1/strategies/versions",
        "/api/v1/backtests",
        "/api/v1/optimizations",
        "/api/v1/execution/paper-runs",
        "/api/v1/execution/live-runs",
        "/api/v1/execution/orders",
        "/api/v1/execution/positions",
        "/api/v1/risk/profiles",
        "/api/v1/risk/events",
        "/api/v1/reviews",
        "/api/v1/failures",
        "/api/v1/ingestion/jobs",
        "/api/v1/agents/tasks",
    ]:
        resp = api_client.get(path)
        assert resp.status_code == 200
        assert isinstance(resp.json()["items"], list)
        assert isinstance(resp.json()["total"], int)
