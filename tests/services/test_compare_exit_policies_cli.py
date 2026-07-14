from __future__ import annotations

from copy import deepcopy

from scripts.compare_exit_policies_cli import (
    CANONICAL_EXIT_LADDER_LEVELS,
    ENTRY_BASELINE_FROZEN,
    ENTRY_BASELINE_LIVE,
    FROZEN_2026_07_12_TECHNICAL_RULES,
    _base_rules,
    _parse_end_at,
    build_fixed_and_ladder_policies,
)
from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_RULES
from services.validation.technical_replay import EXIT_MODE_EXIT_LADDER, EXIT_MODE_FIXED_2R


def test_fixed_and_ladder_policies_split_exit_side_without_mutating_source() -> None:
    base = deepcopy(AUTO_PAPER_TECHNICAL_RULES)
    snapshot = deepcopy(base)

    fixed, ladder = build_fixed_and_ladder_policies(base)

    assert fixed.exit_mode == EXIT_MODE_FIXED_2R
    assert fixed.exit_rules == {}
    assert set(fixed.takeprofit_rules) == {"risk_reward"}
    assert ladder.exit_mode == EXIT_MODE_EXIT_LADDER
    # The originating dict must be untouched.
    assert base == snapshot


def test_ladder_policy_reinjects_canonical_ladder_when_live_baseline_dropped_it() -> None:
    # Live rules had their ladder reverted (2026-07); the ladder arm must still
    # carry a partial-close ladder so the A/B stays meaningful.
    live = deepcopy(AUTO_PAPER_TECHNICAL_RULES)
    assert "exit_ladder" not in live["takeprofit_rules"]

    _fixed, ladder = build_fixed_and_ladder_policies(live)

    assert ladder.takeprofit_rules["exit_ladder"] == CANONICAL_EXIT_LADDER_LEVELS
    assert ladder.takeprofit_rules["remainder_trail_after_r"] == 2.5


def test_frozen_baseline_preserves_audit_time_ladder_and_costs() -> None:
    _fixed, ladder = build_fixed_and_ladder_policies(deepcopy(FROZEN_2026_07_12_TECHNICAL_RULES))

    # Frozen config already ships the audit-time ladder; it must be kept verbatim.
    assert ladder.takeprofit_rules["exit_ladder"] == [
        {"r_multiple": 1.0, "close_fraction": 0.4},
        {"r_multiple": 1.5, "close_fraction": 0.3},
    ]
    entry = FROZEN_2026_07_12_TECHNICAL_RULES["entry_rules"]
    assert entry["core_fee_bps"] == 10.0
    assert entry["standard_fee_bps"] == 18.0
    assert "fvg" not in entry["enabled_signals"]
    assert "mtf_ma" not in entry["enabled_signals"]
    assert len(entry["enabled_signals"]) == 8


def test_base_rules_selects_frozen_vs_live() -> None:
    assert _base_rules(ENTRY_BASELINE_FROZEN)["entry_rules"]["core_fee_bps"] == 10.0
    assert (
        _base_rules(ENTRY_BASELINE_LIVE)["entry_rules"]["core_fee_bps"]
        == AUTO_PAPER_TECHNICAL_RULES["entry_rules"]["core_fee_bps"]
    )


def test_parse_end_at_assumes_utc_when_naive() -> None:
    parsed = _parse_end_at("2026-07-12T08:00:00")
    assert parsed.tzinfo is not None
    assert parsed.isoformat() == "2026-07-12T08:00:00+00:00"
