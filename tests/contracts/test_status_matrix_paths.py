from __future__ import annotations

from pathlib import Path


def test_status_matrix_references_existing_core_paths() -> None:
    root = Path(__file__).resolve().parents[2]
    required_paths = [
        "shared/models/backtest.py",
        "services/validation/walk_forward.py",
        "services/validation/stress_scenarios.py",
        "apps/api/routers/system.py",
        "apps/api/routers/notifications.py",
        "services/data/capabilities.py",
    ]
    for path in required_paths:
        assert (root / path).exists(), path


def test_status_docs_describe_current_phase_and_next_p1_order() -> None:
    root = Path(__file__).resolve().parents[2]
    phase_roadmap = (root / "docs/roadmap/phase-roadmap.md").read_text(encoding="utf-8")
    status_matrix = (root / "docs/architecture/implementation-status-matrix.md").read_text(encoding="utf-8")
    delivery_checklist = (root / "docs/ops/delivery-checklist.md").read_text(encoding="utf-8")

    for text in (phase_roadmap, status_matrix, delivery_checklist):
        assert "Phase 0 完成 + 第一批 P1 落地" in text

    assert "1. Celery Beat / 7x24 调度" in phase_roadmap
    assert "2. 前端管理台补齐" in phase_roadmap
    assert "3. B/C/D 级数据源接入" in phase_roadmap
