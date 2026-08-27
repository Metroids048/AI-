from __future__ import annotations

from scripts.run_qinxiongmao_research import _fragment_accounting, _mechanizability_audit, _source_audit


def test_text_only_spec_fails_mechanizability_gate() -> None:
    spec = {
        "entry_long": {"condition": "趋势突破后再找信号"},
        "exit": {"condition": "看情况退出"},
        "stop_loss": {"condition": "止损放在关键位"},
        "position_sizing": {"condition": "控制仓位"},
        "timeframes": ["1小时"],
        "regime_filter": {"value": ["趋势"]},
    }
    result = _mechanizability_audit(spec)
    assert result["passed"] is False
    assert "entry_not_structured" in result["failures"]
    assert "instrument_not_explicit" in result["failures"]


def test_source_audit_requires_timestamp_evidence() -> None:
    spec = {"source_rules": ["RULE-1"], "source_videos": ["video-1"], "source_provenance": "SPEAKER_EXPLICIT"}
    result = _source_audit(spec, {"RULE-1": {"source_timestamps": []}})
    assert result["passed"] is False
    assert "missing_timestamp_evidence" in result["failures"]


def test_fragment_accounting_never_synthesizes_topical_overlap() -> None:
    rows = [
        {"rule_id": "RULE-a", "source_videos": ["a"], "name": "顺势回调"},
        {"rule_id": "RULE-b", "source_videos": ["b"], "name": "顺势回调"},
    ]
    result = _fragment_accounting(rows)
    assert result["input_incomplete_rules"] == 2
    assert result["synthesized_count"] == 0
    assert result["decision"] == "NO_SYNTHESIS_WITHOUT_EXPLICIT_AUTHOR_LINK"
