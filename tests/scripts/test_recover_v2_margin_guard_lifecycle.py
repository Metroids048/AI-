"""Contract tests for the explicit read-only margin-guard recovery command."""

from __future__ import annotations

import pytest

from scripts.recover_v2_margin_guard_lifecycle import expected_guard_side


def test_margin_guard_side_is_the_opposite_of_the_persisted_entry_direction() -> None:
    assert expected_guard_side("long") == "sell"
    assert expected_guard_side("short") == "buy"


def test_margin_guard_recovery_rejects_unknown_entry_direction() -> None:
    with pytest.raises(ValueError, match="does not support"):
        expected_guard_side("flat")
