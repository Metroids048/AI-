"""Architecture guards for the legacy/V2 boundary (plan section 15.2).

These tests are the mechanism behind the AGENTS.md "legacy pipeline freeze"
rule. A documentation-only rule is forgotten across long tasks and context
compaction; these tests fail the build instead.

Guards:
1. V2 never imports a frozen legacy execution module.
2. Frozen legacy modules never import V2 (no reverse coupling either).
3. Only one Testnet order writer can be active at a time.
4. Frozen legacy modules do not grow new business logic.

Guard 4 is intentionally a *size ceiling*, not a diff check: the freeze allows
verified ghost-position guards, deprecation markers, and legacy-writer shutdown,
but not new strategy conditions, new AI branches, new protection algorithms, new
reconciliation states, or new sampling lanes. A meaningful line-count increase in
these files means new logic landed somewhere it must not.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

LEGACY_FROZEN_MODULES = (
    "services/execution/paper_cycle_orchestrator.py",
    "services/execution/paper_exchange_execution.py",
    "services/execution/paper_order_lifecycle.py",
    "services/execution/paper_signal.py",
)

# Frozen size ceilings, captured 2026-07-28 at the V2 rebuild baseline.
# Raising any of these numbers requires an explicit operator decision: it means
# new logic was added to a file the freeze declares closed.
LEGACY_FROZEN_LINE_CEILINGS = {
    "services/execution/paper_cycle_orchestrator.py": 2668,
    "services/execution/paper_exchange_execution.py": 1675,
    "services/execution/paper_order_lifecycle.py": 420,
    "services/execution/paper_signal.py": 1179,
}

V2_PACKAGE_DIR = REPO_ROOT / "services" / "automated_trading"


def _iter_v2_modules() -> list[Path]:
    return sorted(V2_PACKAGE_DIR.rglob("*.py"))


def _imported_modules(path: Path) -> set[str]:
    """Collect every module name imported by a Python file.

    Uses the AST rather than a text scan so docstrings and comments that merely
    *mention* a legacy module name do not trip the guard.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_v2_modules_exist_to_guard() -> None:
    """Sanity check: the guards below are meaningless if V2 has no modules."""
    modules = _iter_v2_modules()
    assert len(modules) >= 10, f"expected the V2 package to be populated, found {len(modules)} modules"


@pytest.mark.parametrize(
    "forbidden",
    [
        "services.execution.paper_cycle_orchestrator",
        "services.execution.paper_order_lifecycle",
        "services.execution.paper_exchange_execution",
        "services.execution.paper_signal",
    ],
)
def test_v2_does_not_import_frozen_legacy_module(forbidden: str) -> None:
    """V2 must not reach into any frozen legacy execution module.

    V2 may reuse ``gateway``, ``order_normalizer``, ``scheduler_coordination``
    and the strategy repository, but only through explicit adapters. The frozen
    orchestrator/lifecycle/execution/signal modules are off limits entirely.
    """
    offenders: list[str] = []
    for module_path in _iter_v2_modules():
        if forbidden in _imported_modules(module_path):
            offenders.append(str(module_path.relative_to(REPO_ROOT)))

    assert not offenders, (
        f"V2 modules import frozen legacy module {forbidden}: {offenders}. "
        "V2 must not depend on the frozen paper_* pipeline."
    )


@pytest.mark.parametrize("legacy_rel_path", LEGACY_FROZEN_MODULES)
def test_frozen_legacy_module_does_not_import_v2(legacy_rel_path: str) -> None:
    """The freeze runs both ways: legacy must not gain V2 dependencies either.

    If a frozen file imported V2, new business logic would be flowing back into
    a file that is supposed to be closed, and the two writers would couple.
    """
    path = REPO_ROOT / legacy_rel_path
    assert path.exists(), f"frozen legacy module missing: {legacy_rel_path}"

    v2_imports = {name for name in _imported_modules(path) if name.startswith("services.automated_trading")}

    assert not v2_imports, (
        f"{legacy_rel_path} imports V2 modules {sorted(v2_imports)}; the frozen legacy pipeline must not depend on V2."
    )


@pytest.mark.parametrize("legacy_rel_path", LEGACY_FROZEN_MODULES)
def test_legacy_execution_files_receive_no_new_business_dependencies(legacy_rel_path: str) -> None:
    """Frozen legacy modules must not grow.

    A line-count increase means new logic landed in a file the freeze closed.
    Allowed changes (ghost-position guards, deprecation markers, legacy-writer
    shutdown, deletions) do not push these files past their baseline.
    """
    path = REPO_ROOT / legacy_rel_path
    ceiling = LEGACY_FROZEN_LINE_CEILINGS[legacy_rel_path]
    actual = len(path.read_text(encoding="utf-8").splitlines())

    assert actual <= ceiling, (
        f"{legacy_rel_path} grew to {actual} lines, above its frozen ceiling of {ceiling}. "
        "New business logic belongs in services/automated_trading/, not in the frozen "
        "paper_* pipeline. If this growth is a deliberate operator decision, raise the "
        "ceiling in LEGACY_FROZEN_LINE_CEILINGS in the same commit."
    )


def test_only_one_testnet_order_writer_is_active() -> None:
    """Engine activation must never authorize two Testnet writers at once.

    ``v2_active`` disables the legacy writer. ``legacy`` and ``v2_shadow`` leave
    the legacy writer enabled, but shadow never submits, so exactly one writer
    can ever submit a Testnet order.
    """
    from services.automated_trading.infrastructure.runtime_lock import (
        EngineActivation,
        resolve_engine_activation,
    )

    class _Settings:
        binance_use_testnet = True
        live_trading_enabled = False
        binance_auto_execute = True

        def __init__(self, engine: str) -> None:
            self.automated_trading_engine = engine

    for engine in ("legacy", "v2_shadow", "v2_active"):
        config = resolve_engine_activation(_Settings(engine))

        v2_can_submit = config.v2_activation is EngineActivation.ACTIVE
        legacy_can_submit = config.allow_legacy_writer

        assert not (v2_can_submit and legacy_can_submit), (
            f"engine={engine} authorizes both V2 and the legacy writer to submit "
            "Testnet orders; only one writer is allowed."
        )

    # Shadow specifically must never be treated as a submitting writer.
    shadow = resolve_engine_activation(_Settings("v2_shadow"))
    assert shadow.v2_activation is EngineActivation.SHADOW
    assert shadow.v2_activation is not EngineActivation.ACTIVE


def test_mainnet_cannot_be_configured_for_v2() -> None:
    """V2 has no mainnet implementation; it must be unconfigurable, not merely off."""
    from services.automated_trading.infrastructure.runtime_lock import resolve_engine_activation

    class _Settings:
        automated_trading_engine = "v2_active"
        binance_auto_execute = True

        def __init__(self, *, use_testnet: bool, live_enabled: bool) -> None:
            self.binance_use_testnet = use_testnet
            self.live_trading_enabled = live_enabled

    with pytest.raises(ValueError, match="BINANCE_USE_TESTNET"):
        resolve_engine_activation(_Settings(use_testnet=False, live_enabled=False))

    with pytest.raises(ValueError, match="LIVE_TRADING_ENABLED"):
        resolve_engine_activation(_Settings(use_testnet=True, live_enabled=True))
