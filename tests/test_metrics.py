"""Unit tests for src.backtest.metrics — pure trade-list math."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from src.backtest.metrics import compute_metrics
from src.engine.paper_broker import (
    ClosedTrade,
    CloseReason,
    Position,
    PositionStatus,
)
from src.ict.structure import Trend

UTC = timezone.utc


def _trade(pnl: float, closed_offset_min: int = 0,
           symbol: str = "BTCUSDT") -> ClosedTrade:
    base_ts = datetime(2026, 5, 11, 12, 0, tzinfo=UTC)
    closed_ts = base_ts + timedelta(minutes=closed_offset_min)
    pos = Position(
        id=f"{symbol}_{closed_offset_min}",
        symbol=symbol,
        side=Trend.BULL,
        requested_entry=100.0,
        fill_entry=100.0,
        stop_loss=99.0,
        take_profit=102.0,
        size=1.0,
        risk_amount=1.0,
        entry_fee=0.04,
        status=PositionStatus.CLOSED,
        opened_ts=base_ts,
        filled_ts=base_ts,
        closed_ts=closed_ts,
        close_reason=CloseReason.TP if pnl > 0 else CloseReason.SL,
        exit_price=102.0 if pnl > 0 else 99.0,
        exit_fee=0.04,
        pnl=pnl,
        setup_name="sweep_fvg",
        meta={},
    )
    return ClosedTrade(position=pos, pnl_pct_of_equity=pnl / 10_000.0)


# ============================== empty ====================================== #


def test_empty_trades_returns_zero_metrics():
    m = compute_metrics([], initial_equity=10_000.0)
    assert m.total_trades == 0
    assert m.win_rate == 0.0
    assert m.profit_factor == 0.0
    assert m.final_equity == 10_000.0


# ============================== basic counts ============================== #


def test_win_loss_counts():
    trades = [_trade(50, 1), _trade(-30, 2), _trade(20, 3), _trade(-10, 4)]
    m = compute_metrics(trades, initial_equity=10_000.0)
    assert m.total_trades == 4
    assert m.wins == 2
    assert m.losses == 2
    assert m.breakevens == 0
    assert m.win_rate == 0.5


def test_breakevens_counted_separately():
    trades = [_trade(50, 1), _trade(0, 2), _trade(-30, 3)]
    m = compute_metrics(trades, initial_equity=10_000.0)
    assert m.breakevens == 1
    assert m.wins == 1
    assert m.losses == 1
    # win_rate counts wins / total, not vs losses+wins
    assert m.win_rate == pytest.approx(1 / 3)


# ============================== profit factor ============================== #


def test_profit_factor_basic():
    trades = [_trade(100, 1), _trade(-50, 2)]
    m = compute_metrics(trades, initial_equity=10_000.0)
    assert m.gross_profit == 100.0
    assert m.gross_loss == -50.0
    assert m.profit_factor == 2.0


def test_profit_factor_inf_when_no_losses():
    trades = [_trade(100, 1), _trade(50, 2)]
    m = compute_metrics(trades, initial_equity=10_000.0)
    assert math.isinf(m.profit_factor)


# ============================== equity & drawdown ========================= #


def test_equity_curve_and_final_equity():
    trades = [_trade(100, 1), _trade(-50, 2), _trade(80, 3)]
    m = compute_metrics(trades, initial_equity=10_000.0)
    assert len(m.equity_curve) == 3
    assert m.equity_curve[0].equity == 10_100.0
    assert m.equity_curve[1].equity == 10_050.0
    assert m.equity_curve[2].equity == 10_130.0
    assert m.final_equity == 10_130.0
    assert m.return_pct == pytest.approx(1.30)


def test_max_drawdown_from_peak():
    """Equity goes 10000 → 10500 (peak) → 10200 (DD 300) → 10800.
    Largest DD is 300 (≈ 2.86% of peak 10500)."""
    trades = [_trade(500, 1), _trade(-300, 2), _trade(600, 3)]
    m = compute_metrics(trades, initial_equity=10_000.0)
    assert m.max_drawdown == pytest.approx(300.0)
    assert m.max_drawdown_pct == pytest.approx(300 / 10_500 * 100, abs=0.01)


def test_no_drawdown_when_monotonic():
    trades = [_trade(100, 1), _trade(50, 2), _trade(200, 3)]
    m = compute_metrics(trades, initial_equity=10_000.0)
    assert m.max_drawdown == 0.0


# ============================== avg/largest =============================== #


def test_avg_and_largest_win_loss():
    trades = [_trade(100, 1), _trade(60, 2), _trade(-40, 3), _trade(-80, 4)]
    m = compute_metrics(trades, initial_equity=10_000.0)
    assert m.avg_win == 80.0
    assert m.avg_loss == -60.0
    assert m.largest_win == 100.0
    assert m.largest_loss == -80.0


# ============================== duration ================================== #


def test_duration_days():
    trades = [
        _trade(100, closed_offset_min=0),                       # day 0
        _trade(-50, closed_offset_min=60 * 24 * 3),            # day 3
    ]
    m = compute_metrics(trades, initial_equity=10_000.0)
    assert m.duration_days == pytest.approx(3.0)
