"""Explicit eligibility rules for recurring Paper runtime cycles."""

from __future__ import annotations

from shared.models import PaperRun


def is_auto_scheduled_paper_run(run: PaperRun) -> bool:
    """Only explicitly authorized running PaperRuns belong to the 7x24 loop."""
    return run.paper_status == "running" and bool(run.execution_profile.get("auto_schedule_enabled", False))
