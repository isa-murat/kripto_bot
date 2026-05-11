"""Run the same backtest across multiple symbols and combine results.

Each symbol gets its own independent paper portfolio (starts at the same
initial equity). Per-symbol metrics + per-symbol expectancy + a pooled
portfolio expectancy are printed and optionally written to JSON.

Usage:
    python -m scripts.multi_symbol_backtest --from 2025-11-01 --no-killzone
    python -m scripts.multi_symbol_backtest \\
        --symbols BTCUSDT,ETHUSDT,SOLUSDT \\
        --from 2025-11-01 --no-killzone \\
        --output data/backtest/multi_run1.json
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import typer

from src.backtest.metrics import BacktestMetrics, format_metrics
from src.backtest.runner import STRATEGY_REGISTRY, run_backtest, trade_to_dict
from src.config import REPO_ROOT, get_settings, get_strategy_params
from src.utils.logging import setup_logging
from src.utils.rr_metrics import calculate_expectancy, format_expectancy

UTC = timezone.utc

app = typer.Typer(add_completion=False)


def _parse_date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


@app.command()
def main(
    symbols: str = typer.Option("BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT"),
    from_: str = typer.Option(..., "--from"),
    to: str | None = typer.Option(None),
    no_killzone: bool = typer.Option(False),
    initial_equity: float = typer.Option(10_000.0),
    strategy: str = typer.Option("sweep_fvg", help=f"Strategy: {list(STRATEGY_REGISTRY)}"),
    output: Path | None = typer.Option(None, help="JSON output for combined results."),
    output_prefix: str | None = typer.Option(
        None,
        help="If set (e.g. 'run10'), write per-symbol JSON to "
             "data/backtest/{symbol}_{prefix}.json + combined to multi_{prefix}.json.",
    ),
) -> None:
    setup_logging()
    from_dt = _parse_date(from_)
    to_dt = _parse_date(to) if to else datetime.now(tz=UTC)

    if strategy not in STRATEGY_REGISTRY:
        raise typer.BadParameter(
            f"Unknown strategy {strategy!r}. Choose from {list(STRATEGY_REGISTRY)}"
        )
    _, ParamsCls = STRATEGY_REGISTRY[strategy]

    settings = get_settings()
    maker_rate = settings.paper.fee_maker
    taker_rate = settings.paper.fee_taker

    params = ParamsCls.from_strategy_params(get_strategy_params())
    if no_killzone:
        params.require_killzone = False

    sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
    per_symbol: list[dict] = []
    all_trades: list[dict] = []

    for symbol in sym_list:
        typer.echo(f"\n{'=' * 56}\nRunning backtest: {symbol}\n{'=' * 56}")
        try:
            metrics, broker = asyncio.run(run_backtest(
                symbol=symbol, from_dt=from_dt, to_dt=to_dt,
                params=params, initial_equity=initial_equity,
                strategy=strategy,
            ))
        except Exception as e:
            typer.echo(f"  FAILED: {e}")
            continue

        trades = [trade_to_dict(t) for t in broker.closed_trades]
        exp = calculate_expectancy(trades, maker_rate=maker_rate, taker_rate=taker_rate)

        typer.echo(format_metrics(metrics))
        typer.echo("\n" + format_expectancy(exp))

        per_symbol.append({
            "symbol": symbol,
            "metrics": metrics,
            "expectancy": exp,
            "trades": trades,
        })
        all_trades.extend(trades)

        # Per-symbol JSON output (only when --output-prefix is set)
        if output_prefix:
            per_path = REPO_ROOT / "data" / "backtest" / f"{symbol}_{output_prefix}.json"
            per_path.parent.mkdir(parents=True, exist_ok=True)
            per_payload = {
                "symbol": symbol,
                "strategy": strategy,
                "from": from_dt.isoformat(),
                "to": to_dt.isoformat(),
                "params": asdict(params),
                "fee_model": {"maker_rate": maker_rate, "taker_rate": taker_rate},
                "metrics": {
                    k: v for k, v in asdict(metrics).items() if k != "equity_curve"
                },
                "expectancy": exp,
                "trades": trades,
            }
            per_path.write_text(json.dumps(per_payload, indent=2, default=str),
                                encoding="utf-8")
            typer.echo(f"  Wrote per-symbol JSON: {per_path}")

    # ---------- portfolio summary ---------- #
    typer.echo("\n" + "=" * 72)
    typer.echo(" PORTFOLIO SUMMARY (each symbol = independent paper account)")
    typer.echo("=" * 72)
    header = (
        f" {'Symbol':<10} {'N':>4} {'WR%':>6} {'PF':>6} "
        f"{'PnL':>10} {'NetRR':>6} {'BE-WR%':>7} {'Margin':>8} {'Exp/T':>9}"
    )
    typer.echo(header)
    typer.echo("-" * 72)
    total_trades = 0
    total_pnl = 0.0
    total_wins = 0
    total_losses = 0
    total_gross_profit = 0.0
    total_gross_loss = 0.0
    for row in per_symbol:
        m: BacktestMetrics = row["metrics"]
        e = row["expectancy"]
        typer.echo(
            f" {row['symbol']:<10} {m.total_trades:>4d} {m.win_rate * 100:>6.1f} "
            f"{m.profit_factor:>6.2f} {m.total_pnl:>+10.2f} "
            f"{e['avg_net_rr']:>6.2f} {e['break_even_wr'] * 100:>7.1f} "
            f"{e['margin_pct']:>+7.1f}p {e['expectancy_per_trade']:>+9.2f}"
        )
        total_trades += m.total_trades
        total_pnl += m.total_pnl
        total_wins += m.wins
        total_losses += m.losses
        total_gross_profit += m.gross_profit
        total_gross_loss += m.gross_loss
    typer.echo("-" * 72)

    portfolio_wr = (total_wins / max(total_wins + total_losses, 1)) * 100
    portfolio_pf = (total_gross_profit / abs(total_gross_loss)) if total_gross_loss < 0 else float("inf")
    typer.echo(
        f" {'TOTAL':<10} {total_trades:>4d} {portfolio_wr:>6.1f} "
        f"{portfolio_pf:>6.2f} {total_pnl:>+10.2f}"
    )
    typer.echo("=" * 72)

    # Pooled expectancy across every closed trade
    pooled = calculate_expectancy(all_trades, maker_rate=maker_rate, taker_rate=taker_rate)
    typer.echo("\n" + format_expectancy(pooled).replace(
        "EXPECTANCY ANALYSIS", "POOLED EXPECTANCY (all symbols combined)"
    ))

    # If --output-prefix was used but --output not given, default the combined
    # path to data/backtest/multi_<prefix>.json so both files share the prefix.
    if output is None and output_prefix:
        output = REPO_ROOT / "data" / "backtest" / f"multi_{output_prefix}.json"

    # ---------- JSON output ---------- #
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "strategy": strategy,
            "from": from_dt.isoformat(),
            "to": to_dt.isoformat(),
            "symbols": sym_list,
            "params": asdict(params),
            "fee_model": {"maker_rate": maker_rate, "taker_rate": taker_rate},
            "per_symbol": [
                {
                    "symbol": row["symbol"],
                    # Strip equity_curve points to keep file size reasonable
                    "metrics": {
                        k: v for k, v in asdict(row["metrics"]).items() if k != "equity_curve"
                    },
                    "expectancy": row["expectancy"],
                    "trades": row["trades"],
                }
                for row in per_symbol
            ],
            "pooled": {
                "total_trades": total_trades,
                "win_rate": portfolio_wr / 100,
                "profit_factor": portfolio_pf,
                "total_pnl": total_pnl,
                "expectancy": pooled,
                "trades": all_trades,
            },
        }
        output.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        typer.echo(f"\nWrote combined results to {output} ({total_trades} trades)")


if __name__ == "__main__":
    app()
