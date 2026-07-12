from __future__ import annotations

from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_RULES, OPERATOR_EXPERIENCE_RULES
from shared.models import RiskProfile


def test_strategy_playbook_exposes_code_backed_rules_and_sources(api_client) -> None:
    response = api_client.get("/api/v1/strategy-library/playbook")

    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["verified_on"] == "2026-07-12"
    assert body["metadata"]["source_documents"]
    assert {channel["channel_id"] for channel in body["channels"]} == {
        "funding_carry",
        "technical_directional",
    }
    assert len(body["technical_signals"]) == 8
    assert [stage["stage_id"] for stage in body["decision_stages"]][-2:] == [
        "gatekeeper",
        "exchange_order",
    ]

    defaults = {item["key"]: item for item in body["position_sizing"]["defaults"]}
    assert (
        defaults["technical_meta_label_min_win_rate"]["value"]
        == AUTO_PAPER_TECHNICAL_RULES["entry_rules"]["meta_label_min_win_rate"]
    )
    assert defaults["operator_risk_per_trade"]["value"] == OPERATOR_EXPERIENCE_RULES["position_rules"]["risk_per_trade"]
    assert defaults["operator_max_leverage"]["value"] == OPERATOR_EXPERIENCE_RULES["position_rules"]["max_leverage"]
    assert defaults["generic_risk_profile_max_leverage"]["value"] == RiskProfile().max_leverage

    sources = {source["source_id"]: source for source in body["external_sources"]}
    for source_id in ("superalgos", "jesse", "nautilus_trader", "qlib", "vectorbt", "openbb"):
        assert source_id in sources
    assert sources["superalgos"]["license"] == "Apache-2.0"
    assert sources["superalgos"]["license_policy"] == "distilled_research_allowed"


def test_roadmap_status_update_is_persisted_and_audited(api_client) -> None:
    item_id = "meta-label-oos-validation"
    updated = api_client.patch(
        f"/api/v1/strategy-library/roadmap-items/{item_id}",
        json={"status": "in_progress", "note": "开始准备样本外数据", "updated_by": "operator"},
    )

    assert updated.status_code == 200
    body = updated.json()
    assert body["status"] == "in_progress"
    assert body["note"] == "开始准备样本外数据"
    assert body["updated_by"] == "operator"
    assert body["audit_history"][-1]["status"] == "in_progress"

    fetched = api_client.get("/api/v1/strategy-library/playbook")
    roadmap = {item["item_id"]: item for item in fetched.json()["roadmap"]}
    assert roadmap[item_id]["status"] == "in_progress"
    assert roadmap[item_id]["audit_history"][-1]["updated_by"] == "operator"


def test_roadmap_update_rejects_unknown_item_invalid_status_and_empty_body(api_client) -> None:
    unknown = api_client.patch(
        "/api/v1/strategy-library/roadmap-items/not-real",
        json={"status": "done"},
    )
    invalid = api_client.patch(
        "/api/v1/strategy-library/roadmap-items/meta-label-oos-validation",
        json={"status": "blocked"},
    )
    empty = api_client.patch(
        "/api/v1/strategy-library/roadmap-items/meta-label-oos-validation",
        json={},
    )

    assert unknown.status_code == 404
    assert invalid.status_code == 422
    assert empty.status_code == 422
