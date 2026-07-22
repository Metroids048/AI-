from datetime import UTC, datetime

from scripts.verify_scheduler_multi_instance_24h import verify


def test_accelerated_24h_verification_has_one_winner_per_slot() -> None:
    result = verify(started_at=datetime(2026, 7, 22, tzinfo=UTC))

    assert result["passed"] is True
    assert result["claimed_slots"] == 96
    assert result["duplicate_winner_slots"] == 0
