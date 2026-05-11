"""Unit tests for PaperBroker (paper trading engine)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.engine.paper_broker import (
    CloseReason,
    PaperBroker,
    PositionStatus,
)
from src.ict.structure import Trend
from src.strategies.sweep_fvg import TradeSignal

UTC = timezone.utc


@pytest.fixture
def broker(tmp_path: Path) -> PaperBroker:
    return PaperBroker(
        initial_equity=10_000.0,
        risk_per_trade_pct=0.01,
        fee_taker=0.0004,
        fee_maker=0.0002,
        slippage_ticks=0,                        # disable slippage for cleaner math
        snapshot_path=tmp_path / "state.json",
    )


def _signal(side: Trend = Trend.BULL,
            entry: float = 100.0,
            sl: float = 99.0,
            tp: float = 102.0,
            symbol: str = "BTCUSDT",
            bar_index: int = 1) -> TradeSignal:
    risk = abs(entry - sl)
    rr = abs(tp - entry) / risk if risk > 0 else 0.0
    return TradeSignal(
        symbol=symbol, side=side,
        entry_price=entry, stop_loss=sl, take_profit=tp,
        rr=rr,
        setup_name="sweep_fvg",
        bar_index=bar_index,
        ts=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
        meta={},
    )


# ============================== sizing ====================================== #


def test_open_from_signal_creates_pending_position(broker: PaperBroker):
    pos = broker.open_from_signal(_signal())
    assert pos is not None
    assert pos.status == PositionStatus.PENDING
    assert pos.symbol == "BTCUSDT"
    assert pos.side == Trend.BULL
    assert pos.fill_entry is None
    assert broker.total_active() == 1


def test_size_matches_risk_pct(broker: PaperBroker):
    """Risk = equity × pct = 10_000 × 0.01 = 100; per-unit risk = 1; size=100."""
    pos = broker.open_from_signal(_signal(entry=100, sl=99, tp=102))
    assert pos is not None
    assert pos.size == pytest.approx(100.0)
    assert pos.risk_amount == pytest.approx(100.0)


def test_zero_risk_signal_rejected(broker: PaperBroker):
    """Entry == SL → zero per-unit risk → reject."""
    pos = broker.open_from_signal(_signal(entry=100, sl=100, tp=102))
    assert pos is None
    assert broker.total_active() == 0


# ============================== fills ====================================== #


def test_fill_when_bar_range_includes_entry(broker: PaperBroker):
    broker.open_from_signal(_signal(entry=100, sl=99, tp=102))
    filled = broker.try_fill_pending(
        "BTCUSDT", bar_high=101, bar_low=99.5,
        bar_ts=datetime(2026, 5, 11, 12, 5, tzinfo=UTC),
    )
    assert len(filled) == 1
    pos = filled[0]
    assert pos.status == PositionStatus.OPEN
    assert pos.fill_entry == pytest.approx(100.0)  # no slippage
    assert pos.filled_ts is not None


def test_no_fill_when_entry_outside_bar(broker: PaperBroker):
    broker.open_from_signal(_signal(entry=100))
    filled = broker.try_fill_pending(
        "BTCUSDT", bar_high=99.5, bar_low=99.0,
        bar_ts=datetime(2026, 5, 11, 12, 5, tzinfo=UTC),
    )
    assert filled == []
    assert len(broker.pending_positions) == 1


def test_fee_deducted_on_fill(broker: PaperBroker):
    broker.open_from_signal(_signal())
    eq_before = broker.equity
    broker.try_fill_pending(
        "BTCUSDT", bar_high=101, bar_low=99.5,
        bar_ts=datetime(2026, 5, 11, 12, 5, tzinfo=UTC),
    )
    # Entry is a limit at FVG mid → maker rate.
    # fee = entry * size * maker = 100 * 100 * 0.0002 = 2.0
    assert broker.equity == pytest.approx(eq_before - 2.0)
    assert broker.open_positions[0].entry_fee == pytest.approx(2.0)


# ============================== close & PnL ================================ #


def test_close_long_at_tp_realizes_profit(broker: PaperBroker):
    broker.open_from_signal(_signal(entry=100, sl=99, tp=102))
    broker.try_fill_pending("BTCUSDT", 101, 99.5,
                            datetime(2026, 5, 11, 12, 5, tzinfo=UTC))
    pos = broker.open_positions[0]
    eq_after_fill = broker.equity   # 9998.0 (entry fee 2.0 = maker)
    trade = broker.close_position(
        pos, exit_price=102.0, reason=CloseReason.TP,
        bar_ts=datetime(2026, 5, 11, 12, 30, tzinfo=UTC),
    )
    # gross = (102-100)*100 = 200; entry fee (maker) 2.0; TP exit (maker) 102*100*0.0002 = 2.04
    # pnl (full) = 200 - 2.0 - 2.04 = 195.96
    assert trade.position.pnl == pytest.approx(195.96, abs=0.01)
    assert trade.position.exit_fee == pytest.approx(2.04, abs=0.01)
    assert broker.equity == pytest.approx(eq_after_fill + 200 - 2.04, abs=0.01)
    assert pos.status == PositionStatus.CLOSED
    assert pos.close_reason == CloseReason.TP
    assert broker.total_active() == 0


def test_close_long_at_sl_realizes_loss(broker: PaperBroker):
    broker.open_from_signal(_signal(entry=100, sl=99, tp=102))
    broker.try_fill_pending("BTCUSDT", 101, 99.5,
                            datetime(2026, 5, 11, 12, 5, tzinfo=UTC))
    pos = broker.open_positions[0]
    trade = broker.close_position(
        pos, exit_price=99.0, reason=CloseReason.SL,
        bar_ts=datetime(2026, 5, 11, 12, 30, tzinfo=UTC),
    )
    # gross = -100; entry fee (maker) 2.0; SL exit (taker) 99*100*0.0004 = 3.96
    # pnl = -100 - 2.0 - 3.96 = -105.96
    assert trade.position.pnl == pytest.approx(-105.96, abs=0.01)
    assert trade.position.exit_fee == pytest.approx(3.96, abs=0.01)


def test_close_short_at_tp_realizes_profit(broker: PaperBroker):
    broker.open_from_signal(_signal(side=Trend.BEAR, entry=100, sl=101, tp=98))
    broker.try_fill_pending("BTCUSDT", 100.5, 99.5,
                            datetime(2026, 5, 11, 12, 5, tzinfo=UTC))
    pos = broker.open_positions[0]
    trade = broker.close_position(
        pos, exit_price=98.0, reason=CloseReason.TP,
        bar_ts=datetime(2026, 5, 11, 12, 30, tzinfo=UTC),
    )
    # gross = (100-98)*100 = 200; entry fee (maker) 2.0; TP exit (maker) 98*100*0.0002 = 1.96
    # pnl = 200 - 2.0 - 1.96 = 196.04
    assert trade.position.pnl == pytest.approx(196.04, abs=0.01)
    assert trade.position.exit_fee == pytest.approx(1.96, abs=0.01)


def test_exit_fee_uses_taker_rate_on_sl(broker: PaperBroker):
    """Symmetric check: SL hits should always charge taker, never maker."""
    broker.open_from_signal(_signal(side=Trend.BEAR, entry=100, sl=101, tp=98))
    broker.try_fill_pending("BTCUSDT", 100.5, 99.5,
                            datetime(2026, 5, 11, 12, 5, tzinfo=UTC))
    pos = broker.open_positions[0]
    trade = broker.close_position(
        pos, exit_price=101.0, reason=CloseReason.SL,
        bar_ts=datetime(2026, 5, 11, 12, 30, tzinfo=UTC),
    )
    # SL exit (taker): 101 * 100 * 0.0004 = 4.04
    assert trade.position.exit_fee == pytest.approx(4.04, abs=0.01)


def test_exit_fee_uses_taker_rate_on_expired(broker: PaperBroker):
    """Mark-to-end EXPIRED close on an open position is a market exit → taker."""
    broker.open_from_signal(_signal(entry=100, sl=99, tp=102))
    broker.try_fill_pending("BTCUSDT", 101, 99.5,
                            datetime(2026, 5, 11, 12, 5, tzinfo=UTC))
    pos = broker.open_positions[0]
    trade = broker.close_position(
        pos, exit_price=100.5, reason=CloseReason.EXPIRED,
        bar_ts=datetime(2026, 5, 11, 14, 30, tzinfo=UTC),
    )
    # EXPIRED on OPEN → taker rate.
    # exit fee = 100.5 * 100 * 0.0004 = 4.02
    assert trade.position.exit_fee == pytest.approx(4.02, abs=0.01)


# ============================== persistence ================================ #


def test_pending_ttl_expires_stale_position(broker: PaperBroker):
    """A PENDING position older than the TTL (default 24 × 15m = 6h) is cancelled."""
    broker.open_from_signal(_signal(entry=100.0, sl=99, tp=102))
    assert len(broker.pending_positions) == 1
    # Forward time past TTL (default 6h for 24 × 15m bars)
    later = datetime(2026, 5, 11, 19, 30, tzinfo=UTC)   # 7.5 h later
    cancelled = broker.cancel_expired_pending(later)
    assert len(cancelled) == 1
    assert cancelled[0].close_reason == CloseReason.EXPIRED
    assert broker.total_active() == 0


def test_pending_ttl_keeps_fresh_position(broker: PaperBroker):
    """A PENDING within TTL is left alone."""
    broker.open_from_signal(_signal(entry=100.0, sl=99, tp=102))
    later = datetime(2026, 5, 11, 12, 5, tzinfo=UTC)   # 5 min later
    cancelled = broker.cancel_expired_pending(later)
    assert cancelled == []
    assert broker.total_active() == 1


def test_pending_ttl_reads_config_bars(broker: PaperBroker):
    """TTL = pending_ttl_bars × entry_tf seconds."""
    from src.config import get_settings
    from src.utils.time import timeframe_to_seconds
    s = get_settings()
    expected_secs = s.paper.pending_ttl_bars * timeframe_to_seconds(s.timeframes.entry)
    assert broker._pending_ttl.total_seconds() == expected_secs


# ============================== price-based invalidation =================== #


def test_invalidation_bullish_when_sl_hit_pre_fill(broker: PaperBroker):
    """Bullish PENDING entry=100, SL=99. Bar prints low=98.5 but high=99.5
    (entry never reached) → INVALIDATED."""
    broker.open_from_signal(_signal(side=Trend.BULL, entry=100.0, sl=99.0, tp=102.0))
    invalidated = broker.cancel_invalidated_pending(
        "BTCUSDT", bar_high=99.5, bar_low=98.5,
        bar_ts=datetime(2026, 5, 11, 12, 5, tzinfo=UTC),
    )
    assert len(invalidated) == 1
    assert invalidated[0].close_reason == CloseReason.INVALIDATED
    assert broker.total_active() == 0


def test_invalidation_bearish_when_sl_hit_pre_fill(broker: PaperBroker):
    """Bearish PENDING entry=100, SL=101. Bar high=101.5 but low=100.5
    (entry never reached for short fill) → INVALIDATED."""
    broker.open_from_signal(_signal(side=Trend.BEAR, entry=100.0, sl=101.0, tp=98.0))
    invalidated = broker.cancel_invalidated_pending(
        "BTCUSDT", bar_high=101.5, bar_low=100.5,
        bar_ts=datetime(2026, 5, 11, 12, 5, tzinfo=UTC),
    )
    assert len(invalidated) == 1
    assert invalidated[0].close_reason == CloseReason.INVALIDATED


def test_invalidation_defers_to_fill_when_bar_covers_entry(broker: PaperBroker):
    """If the same bar would also fill the entry, fill takes precedence —
    we let try_fill_pending handle it instead of invalidating."""
    broker.open_from_signal(_signal(side=Trend.BULL, entry=100.0, sl=99.0, tp=102.0))
    # bar covers entry (100) AND prints below SL (98.5) — defer to fill
    invalidated = broker.cancel_invalidated_pending(
        "BTCUSDT", bar_high=100.5, bar_low=98.5,
        bar_ts=datetime(2026, 5, 11, 12, 5, tzinfo=UTC),
    )
    assert invalidated == []   # fill takes precedence
    assert len(broker.pending_positions) == 1


def test_invalidation_does_not_touch_other_symbols(broker: PaperBroker):
    broker.open_from_signal(_signal(symbol="BTCUSDT", entry=100, sl=99, tp=102))
    # ETH bar that would invalidate if symbol matched
    invalidated = broker.cancel_invalidated_pending(
        "ETHUSDT", bar_high=99.5, bar_low=98.5,
        bar_ts=datetime(2026, 5, 11, 12, 5, tzinfo=UTC),
    )
    assert invalidated == []
    assert broker.total_active() == 1


def test_invalidation_quiet_bar_keeps_pending(broker: PaperBroker):
    """Bar that doesn't touch SL → pending kept."""
    broker.open_from_signal(_signal(side=Trend.BULL, entry=100.0, sl=99.0, tp=102.0))
    invalidated = broker.cancel_invalidated_pending(
        "BTCUSDT", bar_high=99.8, bar_low=99.5,   # well above SL
        bar_ts=datetime(2026, 5, 11, 12, 5, tzinfo=UTC),
    )
    assert invalidated == []
    assert broker.total_active() == 1


def test_state_survives_restart(tmp_path: Path):
    snap = tmp_path / "state.json"
    b1 = PaperBroker(initial_equity=10_000.0, risk_per_trade_pct=0.01,
                     fee_taker=0.0004, slippage_ticks=0, snapshot_path=snap)
    b1.open_from_signal(_signal())
    b1.try_fill_pending("BTCUSDT", 101, 99.5,
                        datetime(2026, 5, 11, 12, 5, tzinfo=UTC))

    # Re-instantiate; should restore state
    b2 = PaperBroker(initial_equity=10_000.0, risk_per_trade_pct=0.01,
                     fee_taker=0.0004, slippage_ticks=0, snapshot_path=snap)
    assert len(b2.open_positions) == 1
    assert b2.equity == pytest.approx(b1.equity)
