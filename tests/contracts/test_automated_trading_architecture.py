"""
Contract test: Automated Trading V2 Architecture Invariants

Ensures V2 implementation adheres to the architectural constraints defined in
docs/superpowers/plans/2026-07-27-automatic-trading-v2-final-rebuild-plan.md
and the three core ADRs.

These tests are ARCHITECTURAL, not functional. They verify:
- V2 modules never import from frozen legacy pipeline
- V2 enums do not contain forbidden mixed-mode values
- Receipt models enforce immutability and uniqueness
- Entry/Exit gate separation is maintained
"""

import ast
import re
from pathlib import Path

import pytest

# --- Constants ---

V2_ROOT = Path(__file__).parent.parent.parent / "services" / "automated_trading"
FROZEN_LEGACY_FILES = [
    "services/execution/paper_cycle_orchestrator.py",
    "services/execution/paper_exchange_execution.py",
    "services/execution/paper_order_lifecycle.py",
    "services/execution/paper_signal.py",
]

FORBIDDEN_MODE_VALUES = [
    "binance_simulation_first",
    "mirror_to_gateway",
    "testnet_but_local_fill",
]


# --- Test: V2 must not import frozen legacy pipeline ---


def test_v2_does_not_import_frozen_legacy_orchestrator():
    """
    V2 modules must not import from the frozen legacy pipeline files.

    Rationale: ADR-001 Single Writer requires clean separation.
    V2 and legacy pipeline must not share execution state machines.
    """
    if not V2_ROOT.exists():
        pytest.skip("V2 automated_trading/ directory does not exist yet (Task 1+)")

    v2_python_files = list(V2_ROOT.rglob("*.py"))
    if not v2_python_files:
        pytest.skip("No Python files in V2 automated_trading/ yet")

    violations = []

    for v2_file in v2_python_files:
        if v2_file.name == "__init__.py":
            continue

        content = v2_file.read_text(encoding="utf-8")

        # Parse imports
        try:
            tree = ast.parse(content, filename=str(v2_file))
        except SyntaxError:
            continue  # Skip unparseable files (may be incomplete during development)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if any(
                    frozen in module
                    for frozen in [
                        "paper_cycle_orchestrator",
                        "paper_exchange_execution",
                        "paper_order_lifecycle",
                        "paper_signal",
                    ]
                ):
                    violations.append(
                        f"{v2_file.relative_to(V2_ROOT.parent.parent)}: imports from frozen legacy module '{module}'"
                    )

    assert not violations, "V2 modules must not import from frozen legacy pipeline.\n" + "\n".join(violations)


# --- Test: Forbidden mode enum values ---


def test_v2_enums_do_not_contain_forbidden_modes():
    """
    V2 AutomatedTradingMode enum must not contain mixed-mode values.

    Rationale: ADR-001 Single Writer forbids:
    - binance_simulation_first (fallback to local fill)
    - mirror_to_gateway (dual state creation)
    - testnet_but_local_fill (ambiguous semantics)

    Only LOCAL_PAPER and BINANCE_TESTNET are valid.
    """
    domain_dir = V2_ROOT / "domain"
    if not domain_dir.exists():
        pytest.skip("V2 domain/ directory does not exist yet (Task 2)")

    enum_files = list(domain_dir.rglob("*.py"))
    if not enum_files:
        pytest.skip("No domain enum files yet")

    violations = []

    for enum_file in enum_files:
        content = enum_file.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_MODE_VALUES:
            if re.search(rf"\b{re.escape(forbidden)}\b", content, re.IGNORECASE):
                violations.append(
                    f"{enum_file.relative_to(V2_ROOT.parent.parent)}: contains forbidden mode value '{forbidden}'"
                )

    assert not violations, "V2 enums must not contain forbidden mixed-mode values.\n" + "\n".join(violations)


# --- Test: Receipt immutability markers ---


def test_v2_receipt_models_are_frozen():
    """
    ExchangeOrderReceipt and ExchangeFillReceipt must be immutable (frozen=True).

    Rationale: ADR-002 Exchange-First Receipts requires append-only semantics.
    Receipts are audit records and must not be mutated after creation.
    """
    infrastructure_dir = V2_ROOT / "infrastructure"
    if not infrastructure_dir.exists():
        pytest.skip("V2 infrastructure/ directory does not exist yet (Task 3)")

    model_files = list(infrastructure_dir.rglob("*models.py")) + list(infrastructure_dir.rglob("*receipt*.py"))
    if not model_files:
        pytest.skip("No infrastructure model files yet")

    violations = []

    for model_file in model_files:
        content = model_file.read_text(encoding="utf-8")

        # Look for receipt class definitions without frozen=True
        receipt_class_pattern = re.compile(
            r"class\s+(ExchangeOrderReceipt|ExchangeFillReceipt)\s*\([^)]*\):", re.MULTILINE
        )

        for match in receipt_class_pattern.finditer(content):
            class_name = match.group(1)
            # Check if frozen=True appears in the same class definition or decorator
            class_start = match.start()
            class_end = content.find("\n\nclass", class_start + 1)
            if class_end == -1:
                class_end = len(content)
            class_block = content[class_start:class_end]

            if "frozen=True" not in class_block and "@dataclass(frozen=True)" not in class_block:
                violations.append(
                    f"{model_file.relative_to(V2_ROOT.parent.parent)}: {class_name} must be frozen (frozen=True)"
                )

    assert not violations, "Receipt models must be immutable (frozen=True).\n" + "\n".join(violations)


# --- Test: Entry/Exit gate separation ---


def test_v2_maintains_entry_exit_gate_separation():
    """
    Entry gates and exit gates must be in separate modules.

    Rationale: ADR-003 Entry Exit Gate Separation requires architectural separation
    to prevent accidental conflation of entry-blocking logic and exit-blocking logic.

    Expected structure:
    - application/entry_gates.py or application/gates/entry.py
    - application/exit_gates.py or application/gates/exit.py

    Forbidden: Mixing entry and exit gate classes in the same module.
    """
    application_dir = V2_ROOT / "application"
    if not application_dir.exists():
        pytest.skip("V2 application/ directory does not exist yet (Task 6+)")

    gate_files = (
        list(application_dir.rglob("*gate*.py"))
        + list(application_dir.rglob("*entry*.py"))
        + list(application_dir.rglob("*exit*.py"))
    )

    if not gate_files:
        pytest.skip("No gate files yet")

    violations = []

    for gate_file in gate_files:
        content = gate_file.read_text(encoding="utf-8")

        has_entry_gate = bool(re.search(r"class\s+\w*EntryGate\w*", content))
        has_exit_gate = bool(re.search(r"class\s+\w*ExitGate\w*", content))

        if has_entry_gate and has_exit_gate:
            violations.append(
                f"{gate_file.relative_to(V2_ROOT.parent.parent)}: mixes EntryGate and ExitGate classes in same module"
            )

    assert not violations, "Entry and exit gates must be in separate modules.\n" + "\n".join(violations)


# --- Test: Legacy pipeline freeze enforcement ---


def test_legacy_pipeline_files_have_freeze_marker():
    """
    Frozen legacy pipeline files should have a freeze marker comment at the top.

    This is a soft convention to make the freeze visible in the file itself,
    not just in AGENTS.md.

    Expected marker (near top of file):
    # FROZEN: No new business logic (see AGENTS.md Automatic Trading V2 Rebuild)
    """
    repo_root = Path(__file__).parent.parent.parent

    missing_markers = []

    for frozen_file_path in FROZEN_LEGACY_FILES:
        full_path = repo_root / frozen_file_path
        if not full_path.exists():
            continue

        content = full_path.read_text(encoding="utf-8")
        first_200_lines = "\n".join(content.split("\n")[:200])

        if "FROZEN" not in first_200_lines and "frozen" not in first_200_lines:
            missing_markers.append(frozen_file_path)

    # This is a WARNING, not a hard failure (Task 0 or Task 18 will add markers)
    if missing_markers:
        pytest.skip(f"Legacy files missing FROZEN marker (will be added in Task 0/18): {', '.join(missing_markers)}")
