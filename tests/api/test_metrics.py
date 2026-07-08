from __future__ import annotations


def test_metrics_endpoint_exposes_runtime_gauges(unauth_api_client) -> None:
    response = unauth_api_client.get("/metrics")
    assert response.status_code == 200
    body = response.text
    assert "scheduler_running" in body
    assert "live_feed_subscribers" in body
    assert "live_feed_dropped_total" in body
