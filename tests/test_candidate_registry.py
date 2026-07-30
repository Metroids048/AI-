"""Tests for candidate strategy registry."""

import pytest

from services.strategy_library.candidates.registry import (
    CANDIDATE_REGISTRY,
    OPERATOR_HEURISTIC_V1,
    OPERATOR_HEURISTIC_V2_RELAXED,
    PANDAS_TA_BROAD_SCREEN,
    get_candidate,
    list_candidates,
)


def test_registry_has_seven_candidates():
    """验证注册表包含全部可回放候选。"""
    assert len(CANDIDATE_REGISTRY) == 7
    assert "operator_heuristic_v1" in CANDIDATE_REGISTRY
    assert "trend_momentum_v1" in CANDIDATE_REGISTRY
    assert "trend_breakout_v1" in CANDIDATE_REGISTRY
    assert "pandas_ta_broad_screen_v1" in CANDIDATE_REGISTRY
    assert "operator_heuristic_v2_relaxed" in CANDIDATE_REGISTRY
    assert "trend_pullback_v1" in CANDIDATE_REGISTRY
    assert "failed_breakout_reversal_v1" in CANDIDATE_REGISTRY


def test_list_candidates():
    """验证list_candidates返回所有候选ID。"""
    candidates = list_candidates()
    assert len(candidates) == 7
    assert set(candidates) == {
        "operator_heuristic_v1",
        "trend_momentum_v1",
        "trend_breakout_v1",
        "pandas_ta_broad_screen_v1",
        "operator_heuristic_v2_relaxed",
        "trend_pullback_v1",
        "failed_breakout_reversal_v1",
    }


def test_get_candidate_success():
    """验证get_candidate能检索候选。"""
    candidate = get_candidate("operator_heuristic_v1")
    assert candidate.candidate_id == "operator_heuristic_v1"
    assert candidate.source == "operator_experience"
    assert candidate.market == "BTC/USDT"
    assert candidate.timeframe == "15m"


def test_get_candidate_unknown_raises_error():
    """验证未知候选ID抛出KeyError。"""
    with pytest.raises(KeyError, match="Unknown candidate"):
        get_candidate("nonexistent_candidate")


def test_operator_heuristic_v1_config_format():
    """验证v1候选配置格式正确。"""
    config = OPERATOR_HEURISTIC_V1.get_config()

    assert "entry_rules" in config
    assert "exit_rules" in config
    assert "stoploss_rules" in config
    assert "takeprofit_rules" in config
    assert "position_rules" in config

    # 验证关键字段
    entry = config["entry_rules"]
    assert entry["entry_timeframe"] == "15m"
    assert entry["direction_timeframe"] == "4h"
    assert entry["state_timeframe"] == "1h"
    assert "enabled_signals" in entry
    assert len(entry["enabled_signals"]) == 10  # 10个手搓指标


def test_pandas_ta_config_format():
    """验证pandas_ta候选配置格式正确。"""
    config = PANDAS_TA_BROAD_SCREEN.get_config()

    assert "entry_rules" in config
    entry = config["entry_rules"]

    # 验证使用pandas_ta指标
    signals = entry["enabled_signals"]
    assert "pandas_ta_supertrend" in signals
    assert "pandas_ta_stoch_rsi" in signals
    assert len(signals) == 2  # 初始只有2个，作为proof of concept


def test_v2_relaxed_config_format():
    """验证v2放宽版配置格式正确。"""
    config = OPERATOR_HEURISTIC_V2_RELAXED.get_config()

    assert "entry_rules" in config
    assert config["entry_rules"]["candidate_id"] == "operator_heuristic_v2_relaxed"
    assert config["entry_rules"]["fusion_method"] == "layered_regime_entry_relaxed"


def test_all_candidates_have_required_metadata():
    """验证所有候选都有必需的元数据。"""
    for candidate_id, candidate in CANDIDATE_REGISTRY.items():
        assert candidate.candidate_id == candidate_id
        assert candidate.source is not None
        assert candidate.hypothesis is not None
        assert candidate.version is not None
        assert candidate.created_at is not None
        assert candidate.market is not None
        assert candidate.timeframe is not None
        assert candidate.config_factory is not None
        assert candidate.execution_eligible is False


def test_failed_breakout_candidate_is_registered_as_research_only() -> None:
    candidate = get_candidate("failed_breakout_reversal_v1")

    assert candidate.lifecycle_state == "RESEARCH_ONLY"
    assert candidate.execution_eligible is False
    assert candidate.get_config()["entry_rules"]["research_only"] is True


def test_candidates_return_valid_configs():
    """验证所有候选都能返回有效配置。"""
    for candidate_id in list_candidates():
        candidate = get_candidate(candidate_id)
        config = candidate.get_config()

        # 基本结构验证
        assert isinstance(config, dict)
        assert "entry_rules" in config
        assert "exit_rules" in config
        assert "stoploss_rules" in config
        assert "takeprofit_rules" in config
        assert "position_rules" in config
