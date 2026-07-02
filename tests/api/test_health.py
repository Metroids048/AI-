"""Smoke tests for the API surface (no DB needed — in-memory seam)."""

from __future__ import annotations


def test_health(api_client) -> None:
    resp = api_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_strategies_crud_seam(api_client) -> None:
    created = api_client.post(
        "/strategies",
        json={"strategy_key": "BTC_Test_v1", "source": "manual", "core_thesis": "t"},
    )
    assert created.status_code == 201
    sid = created.json()["strategy_id"]

    assert api_client.get(f"/strategies/{sid}").status_code == 200
    assert any(s["strategy_id"] == sid for s in api_client.get("/strategies").json())
    assert api_client.delete(f"/strategies/{sid}").status_code == 204
    assert api_client.get(f"/strategies/{sid}").status_code == 404


def test_interface_cluster_skeleton_routes(api_client) -> None:
    for path in [
        "/strategies/ideas",
        "/strategies/drafts",
        "/strategies/versions",
        "/backtests",
        "/optimizations",
        "/paper-runs",
        "/live-runs",
        "/risk/profiles",
        "/risk/events",
        "/reviews",
        "/failures",
        "/ingestion/jobs",
    ]:
        resp = api_client.get(path)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
