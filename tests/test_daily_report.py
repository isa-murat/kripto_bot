"""Tests for format_daily_report — broker state → human-readable summary."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.engine.paper_broker import CloseReason, PaperBroker
from src.ict.structure import Trend
from src.notify.telegram import format_daily_report
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
            symbol: str = "BTCUSDT",
            ts: datetime | None = None,
            bar_index: int = 1) -> TradeSignal:
    return TradeSignal(
        symbol=symbol, side=side,
        entry_price=entry, stop_loss=sl, take_profit=tp,
        rr=2.0, setup_name="sweep_fvg",
        bar_index=bar_index,
        ts=ts or datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
        meta={},
    )


def _open_and_close(broker: PaperBroker, *, side=Trend.BULL,
                    pnl_target: str = "tp",       # "tp" or "sl"
                    symbol: str = "BTCUSDT",
                    closed_at: datetime | None = None,
                    bar_index: int = 1) -> None:
    sig = _signal(side=side, symbol=symbol, bar_index=bar_index)
    broker.open_from_signal(sig)
    broker.try_fill_pending(symbol, 101, 99.5,
                            datetime(2026, 5, 11, 12, 5, tzinfo=UTC))
    pos = broker.open_positions[-1]
    exit_price = sig.take_profit if pnl_target == "tp" else sig.stop_loss
    reason = CloseReason.TP if pnl_target == "tp" else CloseReason.SL
    broker.close_position(pos, exit_price=exit_price, reason=reason,
                          bar_ts=closed_at or datetime(2026, 5, 11, 12, 30, tzinfo=UTC))


# ============================== empty state ================================ #


def test_report_no_trades_renders_message(broker: PaperBroker):
    msg = format_daily_report(broker)
    assert "GÜNLÜK RAPOR" in msg
    assert "Equity:" in msg
    assert "Toplam trade: 0" in msg
    assert "hiç trade yok" in msg
    assert "Açık: 0" in msg
    assert "Pending: 0" in msg


# ============================== with trades ================================ #


def test_report_includes_24h_pnl_and_winrate(broker: PaperBroker):
    now = datetime.now(UTC)
    _open_and_close(broker, pnl_target="tp", bar_index=1, closed_at=now - timedelta(hours=2))
    _open_and_close(broker, pnl_target="sl", bar_index=2, closed_at=now - timedelta(hours=1))
    _open_and_close(broker, pnl_target="tp", bar_index=3, closed_at=now)

    msg = format_daily_report(broker, since=now - timedelta(hours=24))
    assert "Toplam trade: 3" in msg
    assert "(24h)" in msg
    # 2 wins, 1 loss → 67%
    assert "Win rate (24h): 2/3 = 67%" in msg
    assert "P&L (24h):" in msg


def test_report_filters_old_trades_from_24h_window(broker: PaperBroker):
    now = datetime.now(UTC)
    _open_and_close(broker, pnl_target="tp", bar_index=1, closed_at=now - timedelta(days=2))
    _open_and_close(broker, pnl_target="tp", bar_index=2, closed_at=now)

    msg = format_daily_report(broker, since=now - timedelta(hours=24))
    # 2 all-time, 1 in 24h
    assert "Toplam trade: 2 (all-time) | 1 (24h)" in msg


def test_report_lists_open_and_pending_positions(broker: PaperBroker):
    sig = _signal(symbol="BTCUSDT", entry=100, sl=99, tp=102, bar_index=1)
    broker.open_from_signal(sig)
    broker.try_fill_pending("BTCUSDT", 101, 99.5,
                            datetime(2026, 5, 11, 12, 5, tzinfo=UTC))
    # And one PENDING that hasn't been filled
    broker.open_from_signal(_signal(symbol="ETHUSDT", entry=200, sl=198, tp=204, bar_index=2))

    msg = format_daily_report(broker)
    assert "Açık: 1 pozisyon" in msg
    assert "BTCUSDT" in msg
    assert "🟢 LONG" in msg
    assert "Pending: 1" in msg
    assert "ETHUSDT" in msg


def test_report_shows_lifetime_equity_change(broker: PaperBroker):
    now = datetime.now(UTC)
    _open_and_close(broker, pnl_target="tp", bar_index=1, closed_at=now - timedelta(hours=1))
    msg = format_daily_report(broker)
    assert "lifetime" in msg
    # Profit was made, so the sign should be positive somewhere
    assert "+" in msg
