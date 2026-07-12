from __future__ import annotations

from datetime import UTC, datetime, timedelta


def test_ensemble_and_meta_label_api(api_client) -> None:
    series = [float(i % 5) for i in range(220)]
    ensemble_resp = api_client.post(
        "/api/v1/ensembles",
        json={
            "min_history": 200,
            "signals": [
                {
                    "strategy_id": "s1",
                    "direction": "long",
                    "weight": 1.0,
                    "confidence": 0.8,
                    "validation_score": 1.0,
                    "series": series,
                },
                {
                    "strategy_id": "s2",
                    "direction": "long",
                    "weight": 1.0,
                    "confidence": 0.7,
                    "validation_score": 0.2,
                    "series": [value * 3 for value in series],
                },
            ],
        },
    )
    assert ensemble_resp.status_code == 201
    ensemble = ensemble_resp.json()
    assert ensemble["fused_direction"] == "long"
    assert ensemble["raw_votes"][1]["weight"] == 0.25

    signal_time = datetime(2024, 2, 1, tzinfo=UTC)
    label_resp = api_client.post(
        "/api/v1/meta-labels",
        json={
            "ensemble_id": ensemble["ensemble_id"],
            "signal_time": signal_time.isoformat(),
            "training_samples": [
                {
                    "sample_time": (signal_time - timedelta(days=index + 1)).isoformat(),
                    "net_return": 0.01,
                }
                for index in range(20)
            ],
        },
    )
    assert label_resp.status_code == 201
    assert label_resp.json()["bet_decision"] == "bet_taken"
