from __future__ import annotations

from shared.models import RiskProfile


def test_risk_profile_create_get_update_list(api_client) -> None:
    created = api_client.post(
        "/api/v1/risk/profiles",
        json={
            "max_symbol_exposure": 0.12,
            "max_total_exposure": 0.45,
            "max_leverage": 2.5,
            "hard_stop_drawdown_limit": 0.18,
        },
    )
    assert created.status_code == 201
    profile = created.json()
    profile_id = profile["risk_profile_id"]
    assert profile["max_symbol_exposure"] == 0.12

    fetched = api_client.get(f"/api/v1/risk/profiles/{profile_id}")
    assert fetched.status_code == 200
    assert fetched.json()["max_leverage"] == 2.5

    updated = api_client.put(
        f"/api/v1/risk/profiles/{profile_id}",
        json={"max_leverage": 3.0, "daily_loss_limit": 0.025},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["max_leverage"] == 3.0
    assert body["daily_loss_limit"] == 0.025
    assert body["max_symbol_exposure"] == 0.12

    listed = api_client.get("/api/v1/risk/profiles")
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert any(item["risk_profile_id"] == profile_id for item in items)

    missing = api_client.put(
        "/api/v1/risk/profiles/does-not-exist",
        json={"max_leverage": 1.0},
    )
    assert missing.status_code == 404


def test_risk_profile_update_rejects_empty_body(api_client) -> None:
    created = api_client.post("/api/v1/risk/profiles", json=RiskProfile().model_dump(mode="json"))
    profile_id = created.json()["risk_profile_id"]
    response = api_client.put(f"/api/v1/risk/profiles/{profile_id}", json={})
    assert response.status_code == 200
    assert response.json()["risk_profile_id"] == profile_id
