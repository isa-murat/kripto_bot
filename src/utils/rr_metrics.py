"""Net RR and expectancy metrics for backtest analysis.

Provides:
- `calculate_net_rr`: fee-adjusted reward-to-risk for a single planned trade.
- `calculate_expectancy`: aggregate expectancy + break-even WR + margin from a
  list of closed trades (dict format compatible with backtest JSON).

Fee model assumes:
- Entry is a limit order at the FVG midpoint → maker fee.
- TP exit is a resting limit order at the target → maker fee.
- SL exit is a stop-market trigger → taker fee.

Both functions tolerate edge cases (zero size, empty inputs) and never raise.
"""

from __future__ import annotations

from typing import Any


def _zero_rr_result() -> dict[str, float]:
    return {
        "gross_rr": 0.0,
        "net_rr": 0.0,
        "entry_fee": 0.0,
        "tp_fee": 0.0,
        "sl_fee": 0.0,
        "total_fees_at_tp": 0.0,
        "net_profit_at_tp": 0.0,
        "net_loss_at_sl": 0.0,
    }


def calculate_net_rr(
    entry: float,
    sl: float,
    tp: float,
    size: float,
    maker_rate: float = 0.0002,
    taker_rate: float = 0.0004,
) -> dict[str, float]:
    """Gross + fee-adjusted net RR for a planned trade.

    Fees:
        entry → maker (limit at FVG mid)
        TP exit → maker (limit at target)
        SL exit → taker (stop-market)

    Side is inferred from tp vs entry: tp > entry → long; tp < entry → short.
    Returns zeros (never raises) when size <= 0 or sl is on the wrong side of
    entry — avoids ZeroDivisionError for malformed setups.
    """
    if size <= 0:
        return _zero_rr_result()

    is_long = tp > entry
    if is_long:
        gross_profit = (tp - entry) * size
        gross_loss = (entry - sl) * size
    else:
        gross_profit = (entry - tp) * size
        gross_loss = (sl - entry) * size

    if gross_loss <= 0 or gross_profit <= 0:
        return _zero_rr_result()

    entry_fee = entry * size * maker_rate
    tp_fee = tp * size * maker_rate
    sl_fee = sl * size * taker_rate

    net_profit_at_tp = gross_profit - entry_fee - tp_fee
    net_loss_at_sl = gross_loss + entry_fee + sl_fee  # positive magnitude

    gross_rr = gross_profit / gross_loss
    net_rr = net_profit_at_tp / net_loss_at_sl if net_loss_at_sl > 0 else 0.0

    return {
        "gross_rr": gross_rr,
        "net_rr": net_rr,
        "entry_fee": entry_fee,
        "tp_fee": tp_fee,
        "sl_fee": sl_fee,
        "total_fees_at_tp": entry_fee + tp_fee,
        "net_profit_at_tp": net_profit_at_tp,
        "net_loss_at_sl": net_loss_at_sl,
    }


def _empty_expectancy() -> dict[str, Any]:
    return {
        "n": 0,
        "win_rate": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "avg_gross_rr": 0.0,
        "avg_net_rr": 0.0,
        "expectancy_per_trade": 0.0,
        "expectancy_r": 0.0,
        "break_even_wr": 0.0,
        "margin_pct": 0.0,
        "sample_too_small": True,
        "thin_margin": False,
        "negative_expectancy": False,
    }


def calculate_expectancy(
    trades: list[dict[str, Any]],
    maker_rate: float = 0.0002,
    taker_rate: float = 0.0004,
    min_sample_size: int = 30,
    thin_margin_threshold_pct: float = 3.0,
) -> dict[str, Any]:
    """Expectancy + break-even WR from a closed-trade list.

    `trades` schema (from backtest JSON): each item must have at least `pnl`;
    optionally `fill_entry`, `stop_loss`, `take_profit`, `size` for R-multiple
    and per-trade net RR computation.

    `margin_pct` is in percentage points (e.g. 21.4 means WR is 21.4 pp above
    break-even). `expectancy_r` is the mean of (pnl / risk_per_trade) across
    trades that have entry+sl+size available.
    """
    if not trades:
        return _empty_expectancy()

    n = len(trades)
    pnls = [float(t.get("pnl", 0.0)) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = len(wins) / n
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    expectancy_per_trade = win_rate * avg_win - (1.0 - win_rate) * avg_loss

    r_vals: list[float] = []
    rrs_gross: list[float] = []
    rrs_net: list[float] = []
    for t in trades:
        entry = t.get("fill_entry")
        sl = t.get("stop_loss")
        tp = t.get("take_profit")
        size = t.get("size", 0.0) or 0.0
        if entry is None or sl is None or size <= 0:
            continue
        risk = abs(float(entry) - float(sl)) * float(size)
        if risk > 0:
            r_vals.append(float(t.get("pnl", 0.0)) / risk)
        if tp is not None:
            rr = calculate_net_rr(
                float(entry), float(sl), float(tp), float(size),
                maker_rate=maker_rate, taker_rate=taker_rate,
            )
            if rr["gross_rr"] > 0:
                rrs_gross.append(rr["gross_rr"])
                rrs_net.append(rr["net_rr"])

    expectancy_r = (sum(r_vals) / len(r_vals)) if r_vals else 0.0
    avg_gross_rr = (sum(rrs_gross) / len(rrs_gross)) if rrs_gross else 0.0
    avg_net_rr = (sum(rrs_net) / len(rrs_net)) if rrs_net else 0.0
    break_even_wr = (1.0 / (1.0 + avg_net_rr)) if avg_net_rr > 0 else 0.0
    margin_pct = (win_rate - break_even_wr) * 100.0

    return {
        "n": n,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "avg_gross_rr": avg_gross_rr,
        "avg_net_rr": avg_net_rr,
        "expectancy_per_trade": expectancy_per_trade,
        "expectancy_r": expectancy_r,
        "break_even_wr": break_even_wr,
        "margin_pct": margin_pct,
        "sample_too_small": n < min_sample_size,
        "thin_margin": 0.0 <= margin_pct < thin_margin_threshold_pct,
        "negative_expectancy": expectancy_per_trade < 0,
    }


def format_expectancy(exp: dict[str, Any]) -> str:
    """Render expectancy dict as a console block, with warning lines appended."""
    width = 56
    rule = "─" * width
    lines = [
        rule,
        " EXPECTANCY ANALYSIS",
        rule,
        f" Avg gross RR:        {exp['avg_gross_rr']:>5.2f}",
        f" Avg net RR:          {exp['avg_net_rr']:>5.2f}  (after maker+taker fees)",
        f" Observed WR:         {exp['win_rate'] * 100:>5.1f}% (N={exp['n']})",
        f" Break-even WR:       {exp['break_even_wr'] * 100:>5.1f}% (based on net RR)",
        f" Margin:              {exp['margin_pct']:>+5.1f} pp",
        f" Expectancy/trade:    ${exp['expectancy_per_trade']:>+8.2f}",
        f" Expectancy in R:     {exp['expectancy_r']:>+5.2f}R",
        rule,
    ]
    if exp.get("sample_too_small"):
        lines.append(" ⚠️  SAMPLE TOO SMALL — results not statistically significant")
    if exp.get("negative_expectancy"):
        lines.append(" ❌ NEGATIVE EXPECTANCY — system loses money long-term")
    elif exp.get("thin_margin"):
        lines.append(" ⚠️  THIN MARGIN — vulnerable to drawdown")
    return "\n".join(lines)
