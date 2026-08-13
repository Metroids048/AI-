"""Safety contracts for the operator-authorized Testnet sizing script."""

from types import SimpleNamespace

import pytest

from scripts import apply_e010_testnet_sizing_profile as sizing_script


def test_pending_snapshot_is_never_silently_replaced() -> None:
    """Concurrent NEXT_CYCLE work must survive a sizing-profile invocation."""
    pending = SimpleNamespace(config_snapshot_id="operator-pending-snapshot")
    snapshots = SimpleNamespace(get_pending=lambda _: pending)

    with pytest.raises(RuntimeError, match="operator-pending-snapshot"):
        sizing_script._require_no_pending_snapshot(snapshots, "paper-run")
