"""Backtest performance metrics — pure functions over closed-trade lists.

Inputs come straight from `PaperBroker.closed_trades` after a backtest run.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from src.engine.paper_broker import ClosedTrade


@dataclass(frozen=True)
class EquityPoint:
    ts: datetime
    equity: float


@dataclass
class BacktestMetrics:
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    breakevens: int = 0
    win_rate: float = 0.0                # 0..1
    gross_profit: float = 0.0
    gross_loss: float = 0.0              # negative number (sum of losing pnls)
    profit_factor: float = 0.0           # gross_profit / |gross_loss|; inf if no losses
    avg_win: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    total_pnl: float = 0.0
    initial_equity: float = 0.0
    final_equity: float = 0.0
    return_pct: float = 0.0              # (final/initial - 1) * 100
    max_drawdown: float = 0.0            # absolute drawdown from peak
    max_drawdown_pct: float = 0.0
    sharpe: float = 0.0                  # crude, per-trade returns Sharpe
    duration_days: float = 0.0
    equity_curve: list[EquityPoint] = field(default_factory=list)


def compute_metrics(
    closed_trades: Iterable[ClosedTrade],
    initial_equity: float,
) -> BacktestMetrics:
    trades = list(closed_trades)
    m = BacktestMetrics(initial_equity=initial_equity, final_equity=initial_equity)

    if not trades:
        return m

    pnls = [t.position.pnl for t in trades]
    m.total_trades = len(trades)
    m.wins = sum(1 for p in pnls if p > 0)
    m.losses = sum(1 for p in pnls if p < 0)
    m.breakevens = sum(1 for p in pnls if p == 0)
    m.win_rate = m.wins / m.total_trades

    win_pnls = [p for p in pnls if p > 0]
    loss_pnls = [p for p in pnls if p < 0]
    m.gross_profit = sum(win_pnls)
    m.gross_loss = sum(loss_pnls)
    if loss_pnls:
        m.profit_factor = m.gross_profit / abs(m.gross_loss)
    else:
        m.profit_factor = math.inf if win_pnls else 0.0
    m.avg_win = (m.gross_profit / len(win_pnls)) if win_pnls else 0.0
    m.avg_loss = (m.gross_loss / len(loss_pnls)) if loss_pnls else 0.0
    m.largest_win = max(win_pnls) if win_pnls else 0.0
    m.largest_loss = min(loss_pnls) if loss_pnls else 0.0
    m.total_pnl = sum(pnls)

    # Equity curve: rolling sum starting from initial_equity, anchored to close ts
    curve: list[EquityPoint] = []
    eq = initial_equity
    for t in sorted(trades, key=lambda x: x.position.closed_ts or x.position.opened_ts):
        eq += t.position.pnl
        curve.append(EquityPoint(
            ts=t.position.closed_ts or t.position.opened_ts,
            equity=eq,
        ))
    m.equity_curve = curve
    m.final_equity = eq
    m.return_pct = ((eq / initial_equity) - 1.0) * 100.0 if initial_equity > 0 else 0.0

    # Max drawdown from running peak
    peak = initial_equity
    max_dd_abs = 0.0
    max_dd_pct = 0.0
    for pt in curve:
        peak = max(peak, pt.equity)
        dd = peak - pt.equity
        dd_pct = (dd / peak) if peak > 0 else 0.0
        if dd > max_dd_abs:
            max_dd_abs = dd
            max_dd_pct = dd_pct
    m.max_drawdown = max_dd_abs
    m.max_drawdown_pct = max_dd_pct * 100.0

    # Per-trade returns Sharpe (no risk-free rate, no annualization).
    # For a more rigorous metric, switch to daily-return Sharpe in Faz 5.
    if len(pnls) >= 2:
        # use return as % of equity at trade time (approximate by initial)
        rets = [p / initial_equity for p in pnls]
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
        std = math.sqrt(var)
        m.sharpe = (mean / std * math.sqrt(len(rets))) if std > 0 else 0.0

    # Duration
    timestamps = [t.position.closed_ts for t in trades if t.position.closed_ts]
    if len(timestamps) >= 2:
        m.duration_days = (max(timestamps) - min(timestamps)).total_seconds() / 86400.0

    return m


def format_metrics(m: BacktestMetrics) -> str:
    """Render a BacktestMetrics as a console-friendly multi-line string."""
    lines = [
        "─" * 56,
        " BACKTEST RESULTS",
        "─" * 56,
        f" Trades:        {m.total_trades:>6}   (W {m.wins} / L {m.losses} / BE {m.breakevens})",
        f" Win rate:      {m.win_rate * 100:>6.2f}%",
        f" Profit factor: {m.profit_factor:>6.2f}",
        f" Total PnL:     {m.total_pnl:>+10.2f}",
        f" Gross profit:  {m.gross_profit:>+10.2f}",
        f" Gross loss:    {m.gross_loss:>+10.2f}",
        f" Avg win:       {m.avg_win:>+10.2f}",
        f" Avg loss:      {m.avg_loss:>+10.2f}",
        f" Largest win:   {m.largest_win:>+10.2f}",
        f" Largest loss:  {m.largest_loss:>+10.2f}",
        "─" * 56,
        f" Initial:       {m.initial_equity:>10.2f}",
        f" Final:         {m.final_equity:>10.2f}",
        f" Return:        {m.return_pct:>+9.2f}%",
        f" Max drawdown:  {m.max_drawdown:>10.2f}  ({m.max_drawdown_pct:.2f}%)",
        f" Sharpe (raw):  {m.sharpe:>10.2f}",
        f" Duration:      {m.duration_days:>10.1f} days",
        "─" * 56,
    ]
    return "\n".join(lines)
