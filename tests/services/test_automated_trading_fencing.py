"""Tests for V2 fencing token mechanism and single-writer enforcement.

Validates:
- Fencing token generation is unique
- Conflict detection works across concurrent cycles
- Legacy Writer startup check blocks when V2 positions exist
- No false positives from old/completed cycles
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from services.automated_trading.application.fencing import (
    assert_no_active_v2_positions,
    check_fencing_conflict,
    generate_fencing_token,
)
from services.automated_trading.domain.enums import V2ExecutionMode, V2PositionState
from services.automated_trading.domain.receipts import FillReceipt
from services.automated_trading.infrastructure.models import Base
from services.automated_trading.infrastructure.repository import AutomatedTradingRepository


@pytest.fixture
def in_memory_db() -> Session:
    """Create an in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()


@pytest.fixture
def repo(in_memory_db: Session) -> AutomatedTradingRepository:
    """Create repository instance."""
    return AutomatedTradingRepository(in_memory_db)


class TestFencingTokenGeneration:
    def test_token_format(self) -> None:
        """Fencing token has expected structure."""
        token = generate_fencing_token("BTC/USDT", V2ExecutionMode.BINANCE_TESTNET)
        parts = token.split("@")
        assert len(parts) == 5  # symbol, mode, instance, timestamp, uuid
        assert "BTC" in token
        assert "BINANCE_TESTNET" in token

    def test_tokens_are_unique(self) -> None:
        """Sequential token generation produces unique tokens."""
        token1 = generate_fencing_token("BTC/USDT", V2ExecutionMode.BINANCE_TESTNET)
        token2 = generate_fencing_token("BTC/USDT", V2ExecutionMode.BINANCE_TESTNET)
        assert token1 != token2

    def test_different_symbols_produce_different_tokens(self) -> None:
        """Tokens for different symbols are distinguishable."""
        btc_token = generate_fencing_token("BTC/USDT", V2ExecutionMode.BINANCE_TESTNET)
        eth_token = generate_fencing_token("ETH/USDT", V2ExecutionMode.BINANCE_TESTNET)
        assert "BTC" in btc_token
        assert "ETH" in eth_token
        assert btc_token != eth_token

    def test_different_modes_produce_different_tokens(self) -> None:
        """Tokens for different execution modes are distinguishable."""
        testnet_token = generate_fencing_token("BTC/USDT", V2ExecutionMode.BINANCE_TESTNET)
        paper_token = generate_fencing_token("BTC/USDT", V2ExecutionMode.LOCAL_PAPER)
        assert "BINANCE_TESTNET" in testnet_token
        assert "LOCAL_PAPER" in paper_token
        assert testnet_token != paper_token


class TestFencingConflictDetection:
    def test_no_conflict_when_no_other_cycles(self, repo: AutomatedTradingRepository, in_memory_db: Session) -> None:
        """No conflict when this is the only cycle."""
        token = generate_fencing_token("BTC/USDT", V2ExecutionMode.BINANCE_TESTNET)
        repo.create_cycle(
            cycle_id="cycle-001",
            symbol="BTC/USDT",
            timeframe="15m",
            bar_timestamp=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            fencing_token=token,
        )
        repo.commit()

        has_conflict, conflicting_token = check_fencing_conflict(
            in_memory_db, "BTC/USDT", V2ExecutionMode.BINANCE_TESTNET, token
        )
        assert not has_conflict
        assert conflicting_token is None

    def test_conflict_detected_with_concurrent_cycle(
        self, repo: AutomatedTradingRepository, in_memory_db: Session
    ) -> None:
        """Conflict detected when another cycle exists for same (symbol, mode)."""
        token1 = generate_fencing_token("BTC/USDT", V2ExecutionMode.BINANCE_TESTNET)
        token2 = generate_fencing_token("BTC/USDT", V2ExecutionMode.BINANCE_TESTNET)

        # Create first cycle
        repo.create_cycle(
            cycle_id="cycle-001",
            symbol="BTC/USDT",
            timeframe="15m",
            bar_timestamp=datetime.now(UTC),
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            fencing_token=token1,
        )
        repo.commit()

        # Check for conflict with second token
        has_conflict, conflicting_token = check_fencing_conflict(
            in_memory_db, "BTC/USDT", V2ExecutionMode.BINANCE_TESTNET, token2
        )
        assert has_conflict
        assert conflicting_token == token1

    def test_no_conflict_with_old_cycle_outside_lookback(
        self, repo: AutomatedTradingRepository, in_memory_db: Session
    ) -> None:
        """No conflict with old cycle outside lookback window."""
        token1 = generate_fencing_token("BTC/USDT", V2ExecutionMode.BINANCE_TESTNET)
        token2 = generate_fencing_token("BTC/USDT", V2ExecutionMode.BINANCE_TESTNET)

        # Create cycle, then force its started_at to 10 minutes ago
        # (started_at defaults to server-side now() and is not settable via
        # create_cycle; override directly on the ORM row to simulate an old cycle).
        cycle = repo.create_cycle(
            cycle_id="cycle-old",
            symbol="BTC/USDT",
            timeframe="15m",
            bar_timestamp=datetime.now(UTC) - timedelta(minutes=10),
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            fencing_token=token1,
        )
        cycle.started_at = datetime.now(UTC) - timedelta(minutes=10)
        repo.commit()

        # Check with lookback=5 minutes (should not see the old cycle)
        has_conflict, conflicting_token = check_fencing_conflict(
            in_memory_db, "BTC/USDT", V2ExecutionMode.BINANCE_TESTNET, token2, lookback_minutes=5
        )
        assert not has_conflict
        assert conflicting_token is None

    def test_no_conflict_with_different_symbol(self, repo: AutomatedTradingRepository, in_memory_db: Session) -> None:
        """No conflict when other cycle is for different symbol."""
        btc_token = generate_fencing_token("BTC/USDT", V2ExecutionMode.BINANCE_TESTNET)
        eth_token = generate_fencing_token("ETH/USDT", V2ExecutionMode.BINANCE_TESTNET)

        repo.create_cycle(
            cycle_id="cycle-btc",
            symbol="BTC/USDT",
            timeframe="15m",
            bar_timestamp=datetime.now(UTC),
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            fencing_token=btc_token,
        )
        repo.commit()

        has_conflict, conflicting_token = check_fencing_conflict(
            in_memory_db, "ETH/USDT", V2ExecutionMode.BINANCE_TESTNET, eth_token
        )
        assert not has_conflict

    def test_no_conflict_with_different_mode(self, repo: AutomatedTradingRepository, in_memory_db: Session) -> None:
        """No conflict when other cycle is for different execution mode."""
        testnet_token = generate_fencing_token("BTC/USDT", V2ExecutionMode.BINANCE_TESTNET)
        paper_token = generate_fencing_token("BTC/USDT", V2ExecutionMode.LOCAL_PAPER)

        repo.create_cycle(
            cycle_id="cycle-testnet",
            symbol="BTC/USDT",
            timeframe="15m",
            bar_timestamp=datetime.now(UTC),
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            fencing_token=testnet_token,
        )
        repo.commit()

        has_conflict, conflicting_token = check_fencing_conflict(
            in_memory_db, "BTC/USDT", V2ExecutionMode.LOCAL_PAPER, paper_token
        )
        assert not has_conflict


class TestLegacyWriterStartupCheck:
    def test_startup_allowed_when_no_v2_positions(self, in_memory_db: Session) -> None:
        """Legacy Writer startup allowed when no V2 positions exist."""
        # Should not raise
        assert_no_active_v2_positions(in_memory_db, V2ExecutionMode.BINANCE_TESTNET)

    def test_startup_blocked_when_v2_position_projected(
        self, repo: AutomatedTradingRepository, in_memory_db: Session
    ) -> None:
        """Legacy Writer startup blocked when V2 has POSITION_PROJECTED."""
        # Create V2 position
        order_id = repo.save_order_submission(
            intent_id="intent-001",
            client_order_id="client-001",
            quantity=0.001,
            leverage=20,
            submitted_at=datetime.now(UTC),
        )
        receipt = FillReceipt(
            intent_id="intent-001",
            exchange_order_id="binance-123",
            trade_ids=("trade-001",),
            filled_quantity=Decimal("0.001"),
            average_fill_price=Decimal("65000.0"),
            total_fee=Decimal("0.65"),
            fill_timestamp=datetime.now(UTC),
        )
        repo.save_fill_receipt(order_id, receipt)
        repo.project_position(
            position_id="pos-001",
            intent_id="intent-001",
            order_record_id=order_id,
            symbol="BTC/USDT",
            direction="long",
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            fill_receipt=receipt,
            state=V2PositionState.POSITION_PROJECTED,
            projected_at=datetime.now(UTC),
        )
        repo.commit()

        # Should raise RuntimeError
        with pytest.raises(RuntimeError, match="Cannot start Legacy Writer.*active V2 managed positions"):
            assert_no_active_v2_positions(in_memory_db, V2ExecutionMode.BINANCE_TESTNET)

    def test_startup_blocked_when_v2_position_protected(
        self, repo: AutomatedTradingRepository, in_memory_db: Session
    ) -> None:
        """Legacy Writer startup blocked when V2 has PROTECTED position."""
        order_id = repo.save_order_submission(
            intent_id="intent-002",
            client_order_id="client-002",
            quantity=0.001,
            leverage=20,
            submitted_at=datetime.now(UTC),
        )
        receipt = FillReceipt(
            intent_id="intent-002",
            exchange_order_id="binance-456",
            trade_ids=("trade-002",),
            filled_quantity=Decimal("0.001"),
            average_fill_price=Decimal("65000.0"),
            total_fee=Decimal("0.65"),
            fill_timestamp=datetime.now(UTC),
        )
        repo.save_fill_receipt(order_id, receipt)
        repo.project_position(
            position_id="pos-002",
            intent_id="intent-002",
            order_record_id=order_id,
            symbol="BTC/USDT",
            direction="long",
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            fill_receipt=receipt,
            state=V2PositionState.PROTECTED,
            projected_at=datetime.now(UTC),
        )
        repo.commit()

        with pytest.raises(RuntimeError, match="Cannot start Legacy Writer.*active V2 managed positions"):
            assert_no_active_v2_positions(in_memory_db, V2ExecutionMode.BINANCE_TESTNET)

    def test_startup_allowed_when_v2_positions_closed(
        self, repo: AutomatedTradingRepository, in_memory_db: Session
    ) -> None:
        """Legacy Writer startup allowed when V2 positions are CLOSED."""
        order_id = repo.save_order_submission(
            intent_id="intent-003",
            client_order_id="client-003",
            quantity=0.001,
            leverage=20,
            submitted_at=datetime.now(UTC),
        )
        receipt = FillReceipt(
            intent_id="intent-003",
            exchange_order_id="binance-789",
            trade_ids=("trade-003",),
            filled_quantity=Decimal("0.001"),
            average_fill_price=Decimal("65000.0"),
            total_fee=Decimal("0.65"),
            fill_timestamp=datetime.now(UTC),
        )
        repo.save_fill_receipt(order_id, receipt)
        repo.project_position(
            position_id="pos-003",
            intent_id="intent-003",
            order_record_id=order_id,
            symbol="BTC/USDT",
            direction="long",
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            fill_receipt=receipt,
            state=V2PositionState.CLOSED,
            projected_at=datetime.now(UTC),
        )
        repo.commit()

        # Should not raise — CLOSED positions do not block
        assert_no_active_v2_positions(in_memory_db, V2ExecutionMode.BINANCE_TESTNET)

    def test_startup_isolation_by_execution_mode(self, repo: AutomatedTradingRepository, in_memory_db: Session) -> None:
        """Legacy Writer check isolates by execution mode."""
        # Create V2 position in BINANCE_TESTNET
        order_id = repo.save_order_submission(
            intent_id="intent-004",
            client_order_id="client-004",
            quantity=0.001,
            leverage=20,
            submitted_at=datetime.now(UTC),
        )
        receipt = FillReceipt(
            intent_id="intent-004",
            exchange_order_id="binance-999",
            trade_ids=("trade-004",),
            filled_quantity=Decimal("0.001"),
            average_fill_price=Decimal("65000.0"),
            total_fee=Decimal("0.65"),
            fill_timestamp=datetime.now(UTC),
        )
        repo.save_fill_receipt(order_id, receipt)
        repo.project_position(
            position_id="pos-004",
            intent_id="intent-004",
            order_record_id=order_id,
            symbol="BTC/USDT",
            direction="long",
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            fill_receipt=receipt,
            state=V2PositionState.PROTECTED,
            projected_at=datetime.now(UTC),
        )
        repo.commit()

        # Check BINANCE_TESTNET: should raise
        with pytest.raises(RuntimeError, match="Cannot start Legacy Writer"):
            assert_no_active_v2_positions(in_memory_db, V2ExecutionMode.BINANCE_TESTNET)

        # Check LOCAL_PAPER: should not raise (different mode)
        assert_no_active_v2_positions(in_memory_db, V2ExecutionMode.LOCAL_PAPER)
