"""Unit tests for PositionManager — SL/TP trigger logic + handler hooks."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.engine.paper_broker import CloseReason, PaperBroker, PositionStatus
from src.engine.position_mgr import PositionManager
from src.ict.structure import Trend
from src.strategies.sweep_fvg import TradeSignal

UTC = timezone.utc


@pytest.fixture
def broker(tmp_path: Path) -> PaperBroker:
    return PaperBroker(
        initial_equity=10_000.0, risk_per_trade_pct=0.01,
        fee_taker=0.0004, slippage_ticks=0,
        snapshot_path=tmp_path / "state.json",
    )


def _signal(side: Trend = Trend.BULL,
            entry: float = 100.0,
            sl: float = 99.0,
            tp: float = 102.0,
            symbol: str = "BTCUSDT") -> TradeSignal:
    return TradeSignal(
        symbol=symbol, side=side,
        entry_price=entry, stop_loss=sl, take_profit=tp,
        rr=abs(tp - entry) / abs(entry - sl),
        setup_name="sweep_fvg",
        bar_index=1,
        ts=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
        meta={},
    )


# ============================== fill via on_bar ============================ #


@pytest.mark.asyncio
async def test_on_bar_fills_pending_when_entry_in_range(broker: PaperBroker):
    broker.open_from_signal(_signal(entry=100))
    filled_calls: list[str] = []

    async def on_filled(pos):
        filled_calls.append(pos.id)

    mgr = PositionManager(broker=broker, on_filled=on_filled)
    await mgr.on_bar(
        symbol="BTCUSDT", bar_high=101, bar_low=99.5,
        bar_ts=datetime(2026, 5, 11, 12, 5, tzinfo=UTC),
    )
    assert len(filled_calls) == 1
    assert broker.open_positions[0].status == PositionStatus.OPEN


# ============================== TP / SL triggers =========================== #


@pytest.mark.asyncio
async def test_long_tp_triggered_when_high_reaches(broker: PaperBroker):
    broker.open_from_signal(_signal(entry=100, sl=99, tp=102))
    closed: list[str] = []

    async def on_closed(pos):
        closed.append(pos.close_reason.value)

    mgr = PositionManager(broker=broker, on_closed=on_closed)
    # Fill bar
    await mgr.on_bar("BTCUSDT", 101, 99.5,
                     datetime(2026, 5, 11, 12, 5, tzinfo=UTC))
    # Bar that hits TP
    await mgr.on_bar("BTCUSDT", 102.5, 101.5,
                     datetime(2026, 5, 11, 12, 10, tzinfo=UTC))
    assert closed == ["take_profit"]
    assert broker.total_active() == 0


@pytest.mark.asyncio
async def test_long_sl_triggered_when_low_reaches(broker: PaperBroker):
    broker.open_from_signal(_signal(entry=100, sl=99, tp=102))
    closed: list[str] = []

    async def on_closed(pos):
        closed.append(pos.close_reason.value)

    mgr = PositionManager(broker=broker, on_closed=on_closed)
    await mgr.on_bar("BTCUSDT", 101, 99.5,
                     datetime(2026, 5, 11, 12, 5, tzinfo=UTC))
    await mgr.on_bar("BTCUSDT", 100, 98.5,
                     datetime(2026, 5, 11, 12, 10, tzinfo=UTC))
    assert closed == ["stop_loss"]


@pytest.mark.asyncio
async def test_long_sl_wins_tie_break_when_both_sl_and_tp_hit(broker: PaperBroker):
    """Conservative tie-break: assume SL fills first inside the same bar."""
    broker.open_from_signal(_signal(entry=100, sl=99, tp=102))
    closed: list[str] = []

    async def on_closed(pos):
        closed.append(pos.close_reason.value)

    mgr = PositionManager(broker=broker, on_closed=on_closed)
    await mgr.on_bar("BTCUSDT", 101, 99.5,
                     datetime(2026, 5, 11, 12, 5, tzinfo=UTC))
    await mgr.on_bar("BTCUSDT", 102.5, 98.5,    # both SL (98.5<=99) and TP (102.5>=102)
                     datetime(2026, 5, 11, 12, 10, tzinfo=UTC))
    assert closed == ["stop_loss"]


@pytest.mark.asyncio
async def test_short_tp_triggered_when_low_reaches(broker: PaperBroker):
    broker.open_from_signal(_signal(side=Trend.BEAR, entry=100, sl=101, tp=98))
    closed: list[str] = []

    async def on_closed(pos):
        closed.append(pos.close_reason.value)

    mgr = PositionManager(broker=broker, on_closed=on_closed)
    await mgr.on_bar("BTCUSDT", 100.5, 99.5,
                     datetime(2026, 5, 11, 12, 5, tzinfo=UTC))
    await mgr.on_bar("BTCUSDT", 99.5, 97.5,
                     datetime(2026, 5, 11, 12, 10, tzinfo=UTC))
    assert closed == ["take_profit"]


@pytest.mark.asyncio
async def test_short_sl_triggered_when_high_reaches(broker: PaperBroker):
    broker.open_from_signal(_signal(side=Trend.BEAR, entry=100, sl=101, tp=98))
    closed: list[str] = []

    async def on_closed(pos):
        closed.append(pos.close_reason.value)

    mgr = PositionManager(broker=broker, on_closed=on_closed)
    await mgr.on_bar("BTCUSDT", 100.5, 99.5,
                     datetime(2026, 5, 11, 12, 5, tzinfo=UTC))
    await mgr.on_bar("BTCUSDT", 101.5, 100.5,
                     datetime(2026, 5, 11, 12, 10, tzinfo=UTC))
    assert closed == ["stop_loss"]


# ============================== handler isolation ========================= #


@pytest.mark.asyncio
async def test_handler_exception_does_not_block_close(broker: PaperBroker):
    broker.open_from_signal(_signal())

    async def bad_handler(pos):
        raise RuntimeError("boom")

    mgr = PositionManager(broker=broker, on_filled=bad_handler, on_closed=bad_handler)
    await mgr.on_bar("BTCUSDT", 101, 99.5,
                     datetime(2026, 5, 11, 12, 5, tzinfo=UTC))
    await mgr.on_bar("BTCUSDT", 102.5, 101,
                     datetime(2026, 5, 11, 12, 10, tzinfo=UTC))
    # Despite handler errors, broker state must reflect the close
    assert broker.total_active() == 0
    assert len(broker.closed_trades) == 1


# ============================== symbol isolation ========================= #


@pytest.mark.asyncio
async def test_other_symbol_bar_does_not_affect_position(broker: PaperBroker):
    broker.open_from_signal(_signal(symbol="BTCUSDT", entry=100, sl=99, tp=102))
    mgr = PositionManager(broker=broker)
    # ETHUSDT bar that would otherwise hit TP if symbol matched
    await mgr.on_bar("ETHUSDT", 200, 105,
                     datetime(2026, 5, 11, 12, 5, tzinfo=UTC))
    assert broker.total_active() == 1
    # Pending still pending (no fill triggered for BTCUSDT)
    assert len(broker.pending_positions) == 1
