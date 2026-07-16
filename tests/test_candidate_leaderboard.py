"""Tests for candidate leaderboard."""

from datetime import datetime, timedelta

import pytest

from services.validation.candidate_leaderboard import (
    CandidateLeaderboard,
    CandidateLeaderboardEntry,
    _compute_net_expectancy_ci,
)
from services.validation.technical_replay import ReplayMetrics


def make_mock_metrics(
    *,
    strategy_key: str = "test_strategy",
    total_trades: int = 100,
    net_expectancy: float = 0.005,
    win_rate: float = 0.55,
    profit_factor: float = 1.3,
) -> ReplayMetrics:
    """创建模拟的ReplayMetrics用于测试。"""
    return ReplayMetrics(
        strategy_key=strategy_key,
        entry_timeframe="15m",
        total_trades=total_trades,
        signal_count=total_trades + 50,
        win_rate=win_rate,
        average_win=0.02,
        average_loss=-0.01,
        average_r=1.5,
        average_hold_hours=12.0,
        ladder_level_hits={},
        gross_return=0.5,
        net_return=0.4,
        net_expectancy=net_expectancy,
        total_fee_bps=50.0,
        total_slippage_bps=10.0,
        cost_share_of_gross_profit=0.2,
        sharpe=1.2,
        profit_factor=profit_factor,
        max_drawdown=0.15,
        evaluation_start=datetime.now(),
        evaluation_end=datetime.now() + timedelta(days=90),
        data_issues=[],
        trades=(),
    )


def test_compute_net_expectancy_ci_small_sample():
    """测试小样本的置信区间计算。"""
    metrics = make_mock_metrics(total_trades=10, net_expectancy=0.01)
    lower, upper = _compute_net_expectancy_ci(metrics)

    # 小样本应该有较宽的置信区间
    assert lower < metrics.net_expectancy
    assert upper > metrics.net_expectancy
    assert upper - lower > 0.01  # 至少1%的区间宽度


def test_compute_net_expectancy_ci_large_sample():
    """测试大样本的置信区间计算。"""
    metrics = make_mock_metrics(total_trades=500, net_expectancy=0.01)
    lower, upper = _compute_net_expectancy_ci(metrics)

    # 大样本应该有较窄的置信区间
    assert lower < metrics.net_expectancy
    assert upper > metrics.net_expectancy
    assert upper - lower < 0.015  # 小于1.5%的区间宽度


def test_compute_net_expectancy_ci_zero_trades():
    """测试无交易时的置信区间。"""
    metrics = make_mock_metrics(total_trades=0, net_expectancy=0.0)
    lower, upper = _compute_net_expectancy_ci(metrics)

    assert lower == 0.0
    assert upper == 0.0


def test_leaderboard_entry_as_dict():
    """测试LeaderboardEntry序列化。"""
    metrics = make_mock_metrics()
    entry = CandidateLeaderboardEntry(
        candidate_id="test_v1",
        source="test_source",
        hypothesis="test hypothesis",
        metrics=metrics,
        net_expectancy_ci_lower=0.003,
        net_expectancy_ci_upper=0.007,
        rank=1,
    )

    data = entry.as_dict()
    assert data["candidate_id"] == "test_v1"
    assert data["rank"] == 1
    assert data["net_expectancy"] == 0.005
    assert data["net_expectancy_ci_lower"] == 0.003
    assert data["net_expectancy_ci_upper"] == 0.007
    assert "metrics" in data


def test_leaderboard_get_winner():
    """测试获取排行榜冠军。"""
    metrics1 = make_mock_metrics(strategy_key="candidate1", net_expectancy=0.01)
    metrics2 = make_mock_metrics(strategy_key="candidate2", net_expectancy=0.005)

    entry1 = CandidateLeaderboardEntry(
        candidate_id="candidate1",
        source="test",
        hypothesis="test",
        metrics=metrics1,
        net_expectancy_ci_lower=0.008,
        net_expectancy_ci_upper=0.012,
        rank=1,
    )

    entry2 = CandidateLeaderboardEntry(
        candidate_id="candidate2",
        source="test",
        hypothesis="test",
        metrics=metrics2,
        net_expectancy_ci_lower=0.003,
        net_expectancy_ci_upper=0.007,
        rank=2,
    )

    leaderboard = CandidateLeaderboard(
        generated_at=datetime.now(),
        entries=(entry1, entry2),
        market_data_summary={"symbols": ["BTC/USDT"], "symbol_count": 1, "total_bars": 1000},
    )

    winner = leaderboard.get_winner()
    assert winner is not None
    assert winner.candidate_id == "candidate1"
    assert winner.rank == 1


def test_leaderboard_get_by_id():
    """测试通过ID查找候选。"""
    metrics = make_mock_metrics()
    entry = CandidateLeaderboardEntry(
        candidate_id="test_candidate",
        source="test",
        hypothesis="test",
        metrics=metrics,
        net_expectancy_ci_lower=0.003,
        net_expectancy_ci_upper=0.007,
        rank=1,
    )

    leaderboard = CandidateLeaderboard(
        generated_at=datetime.now(),
        entries=(entry,),
        market_data_summary={"symbols": ["BTC/USDT"], "symbol_count": 1, "total_bars": 1000},
    )

    found = leaderboard.get_by_id("test_candidate")
    assert found is not None
    assert found.candidate_id == "test_candidate"

    not_found = leaderboard.get_by_id("nonexistent")
    assert not_found is None


def test_leaderboard_as_dict():
    """测试排行榜序列化。"""
    metrics = make_mock_metrics()
    entry = CandidateLeaderboardEntry(
        candidate_id="test",
        source="test",
        hypothesis="test",
        metrics=metrics,
        net_expectancy_ci_lower=0.003,
        net_expectancy_ci_upper=0.007,
        rank=1,
    )

    leaderboard = CandidateLeaderboard(
        generated_at=datetime.now(),
        entries=(entry,),
        market_data_summary={"symbols": ["BTC/USDT"], "symbol_count": 1, "total_bars": 1000},
    )

    data = leaderboard.as_dict()
    assert "generated_at" in data
    assert "entries" in data
    assert len(data["entries"]) == 1
    assert "market_data_summary" in data


@pytest.mark.skip(reason="需要完整的市场数据和回测引擎，暂时跳过集成测试")
def test_run_candidate_leaderboard_integration():
    """集成测试：运行真实的候选排行榜。

    此测试需要：
    1. 真实的市场数据
    2. 完整的回测引擎运行
    3. 较长的执行时间

    在单元测试中跳过，留待手动验证或集成测试套件。
    """
    # 这里会是真实的集成测试代码
    pass
