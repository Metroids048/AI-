"""Unit tests for _detect_orphan_bootstrap_configs in verify_config."""

from __future__ import annotations

import textwrap


def _detect(source: str) -> list:
    from scripts.verify_config import _detect_orphan_bootstrap_configs

    return _detect_orphan_bootstrap_configs(source=textwrap.dedent(source))


CLEAN_SOURCE = (
    "GOOD_RULES = {'entry_rules': {}}\n"
    "def bootstrap_good_strategy():\n"
    "    x = GOOD_RULES\n"
    "def bootstrap_local_paper_runtime():\n"
    "    bootstrap_good_strategy()\n"
)

ORPHAN_SOURCE = (
    "GOOD_RULES = {'entry_rules': {}}\n"
    "ORPHAN_RULES = {'entry_rules': {}}\n"
    "def bootstrap_good_strategy():\n"
    "    x = GOOD_RULES\n"
    "def bootstrap_orphan_strategy():\n"
    "    x = ORPHAN_RULES\n"
    "def bootstrap_local_paper_runtime():\n"
    "    bootstrap_good_strategy()\n"
)

INTENTIONAL_SOURCE = (
    "GOOD_RULES = {'entry_rules': {}}\n"
    "EXCLUDED_RULES = {'entry_rules': {}}\n"
    "def bootstrap_good_strategy():\n"
    "    x = GOOD_RULES\n"
    "def bootstrap_excluded_strategy():\n"
    "    'deliberately NOT wired into bootstrap_local_paper_runtime()'\n"
    "    x = EXCLUDED_RULES\n"
    "def bootstrap_local_paper_runtime():\n"
    "    bootstrap_good_strategy()\n"
)


def test_no_orphans_when_all_configs_wired() -> None:
    result = _detect(CLEAN_SOURCE)
    assert result == [], f"Expected no orphans, got: {result}"


def test_orphan_detected_when_bootstrap_fn_not_in_auto_runtime() -> None:
    result = _detect(ORPHAN_SOURCE)
    rules_names = [r[0] for r in result]
    fn_names = [r[1] for r in result]
    assert "ORPHAN_RULES" in rules_names, f"ORPHAN_RULES not in: {rules_names}"
    assert "bootstrap_orphan_strategy" in fn_names, f"bootstrap_orphan_strategy not in: {fn_names}"


def test_intentionally_excluded_not_flagged() -> None:
    result = _detect(INTENTIONAL_SOURCE)
    rules_names = [r[0] for r in result]
    assert "EXCLUDED_RULES" not in rules_names, f"EXCLUDED_RULES should not be flagged: {rules_names}"


def test_current_repo_finds_swing_orphan() -> None:
    """Integration: scan actual bootstrap.py; expect AUTO_PAPER_SWING_RULES as orphan."""
    from scripts.verify_config import _detect_orphan_bootstrap_configs

    result = _detect_orphan_bootstrap_configs()
    rules_names = [r[0] for r in result]
    assert "AUTO_PAPER_SWING_RULES" in rules_names, f"Expected AUTO_PAPER_SWING_RULES in orphans, got: {rules_names}"
