"""Tests for candidate strategy registry."""

import pytest

from services.strategy_library.candidates.registry import (
    CANDIDATE_REGISTRY,
    OPERATOR_HEURISTIC_V1,
    PANDAS_TA_BROAD_SCREEN,
    OPERATOR_HEURISTIC_V2_RELAXED,
    get_candidate,
    list_candidates,
)


def test_registry_has_three_candidates():
    """验证注册表包含3个初始候选。"""
    assert len(CANDIDATE_REGISTRY) == 3
    assert "operator_heuristic_v1" in CANDIDATE_REGISTRY
    assert "pandas_ta_broad_screen_v1" in CANDIDATE_REGISTRY
    assert "operator_heuristic_v2_relaxed" in CANDIDATE_REGISTRY


def test_list_candidates():
    """验证list_candidates返回所有候选ID。"""
    candidates = list_candidates()
    assert len(candidates) == 3
    assert "operator_heuristic_v1" in candidates


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
    # TODO: 当实现relaxed fusion_method后，验证它使用了新的融合方法
    # assert config["entry_rules"]["fusion_method"] == "layered_regime_entry_relaxed"


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
