"""Explicit eligibility rules for recurring Paper runtime cycles."""

from __future__ import annotations

from shared.models import PaperRun


def is_auto_scheduled_paper_run(run: PaperRun) -> bool:
    """Only explicitly authorized running PaperRuns belong to the 7x24 loop."""
    return run.paper_status == "running" and bool(run.execution_profile.get("auto_schedule_enabled", False))


def is_tight_slot_paper_run(run: PaperRun) -> bool:
    """Armed Binance Testnet runs that must finish Entry inside the 75s pretrade window."""
    if not is_auto_scheduled_paper_run(run):
        return False
    profile = run.execution_profile or {}
    return str(profile.get("execution_mode") or "") == "binance_testnet" and bool(profile.get("mirror_to_gateway"))


def is_deferred_observation_paper_run(run: PaperRun) -> bool:
    """Local / observation auto-runs deferred so they cannot overrun the Entry slot."""
    return is_auto_scheduled_paper_run(run) and not is_tight_slot_paper_run(run)
