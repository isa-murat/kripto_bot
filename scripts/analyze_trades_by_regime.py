"""Classify closed trades by HTF (4h) trend regime at fill time.

For each trade in a backtest JSON:
  1. Look up the 1h Parquet for its symbol, slice up to `filled_ts`
  2. Aggregate to 4h, classify regime with the last 20 4h bars
  3. Tag the trade as 'with_trend' or 'counter_trend' (vs its long/short side)
  4. Print per-symbol + overall breakdowns

Usage:
    python -m scripts.analyze_trades_by_regime --runs data/backtest/multi_run1.json
    python -m scripts.analyze_trades_by_regime --runs data/backtest/    # walks the dir, picks newest matching
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import typer

from src.analysis.trend_classifier import aggregate_to_4h, classify_htf_trend
from src.config import REPO_ROOT, get_env

UTC = timezone.utc

app = typer.Typer(add_completion=False)


# ----------------------------- IO helpers ----------------------------------- #


def _load_trades(runs_path: Path) -> list[dict]:
    """Flatten any of: single-symbol runner JSON, multi-symbol JSON, or a dir."""
    if runs_path.is_dir():
        # Pick the newest *.json in the dir, prefer multi_run* names
        files = sorted(runs_path.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            raise FileNotFoundError(f"No JSON files in {runs_path}")
        runs_path = files[0]
        typer.echo(f"Using newest backtest file: {runs_path.name}\n")

    payload = json.loads(runs_path.read_text(encoding="utf-8"))

    # multi-symbol shape: payload['per_symbol'] is a list of dicts
    if "per_symbol" in payload:
        out: list[dict] = []
        for row in payload["per_symbol"]:
            for t in row.get("trades", []):
                t = dict(t)
                t.setdefault("symbol", row.get("symbol"))
                out.append(t)
        return out

    # single-symbol shape: payload['trades']
    if "trades" in payload:
        sym = payload.get("symbol", "UNKNOWN")
        out = []
        for t in payload["trades"]:
            t = dict(t)
            t.setdefault("symbol", sym)
            out.append(t)
        return out

    raise ValueError(f"Unrecognised backtest JSON shape: {runs_path}")


def _load_1h_parquet(symbol: str) -> pl.DataFrame:
    env = get_env()
    path = REPO_ROOT / env.ohlcv_data_dir / f"{symbol}_1h.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Missing 1h OHLCV: {path}")
    return pl.read_parquet(path).sort("ts")


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s)


# ----------------------------- analysis ------------------------------------- #


def _classify_trade(trade: dict, df_1h: pl.DataFrame, lookback: int) -> str | None:
    """Return 'with_trend' | 'counter_trend' | 'ranging' | None."""
    fill_ts = _parse_ts(trade.get("filled_ts")) or _parse_ts(trade.get("opened_ts"))
    if fill_ts is None:
        return None

    # Strict look-ahead guard: only bars whose CLOSE is at or before fill_ts
    df_past = df_1h.filter(pl.col("ts") <= fill_ts)
    if df_past.height < 20:
        return None

    df_4h = aggregate_to_4h(df_past)
    regime = classify_htf_trend(df_4h, lookback=lookback)
    if regime is None or regime == "ranging":
        return regime  # 'ranging' is a distinct bucket worth seeing

    side = trade.get("side", "").lower()
    is_long = side in ("bull", "long", "buy")
    is_short = side in ("bear", "short", "sell")
    if not (is_long or is_short):
        return None

    if (regime == "bullish" and is_long) or (regime == "bearish" and is_short):
        return "with_trend"
    return "counter_trend"


def _bucket_stats(trades: list[dict]) -> dict:
    """Aggregate trades in a bucket: N, W, L, WR, avg pnl."""
    n = len(trades)
    if n == 0:
        return {"n": 0, "wins": 0, "losses": 0, "wr": 0.0, "avg_pnl": 0.0, "total_pnl": 0.0}
    pnls = [float(t.get("pnl", 0.0)) for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "wr": (wins / n) * 100,
        "avg_pnl": sum(pnls) / n,
        "total_pnl": sum(pnls),
    }


def _print_symbol_table(symbol: str, buckets: dict[str, list[dict]]) -> dict:
    """Render one symbol's regime table. Returns the stats dict for aggregation."""
    typer.echo("\n" + "═" * 67)
    total_n = sum(len(v) for v in buckets.values())
    typer.echo(f" {symbol} — REGIME ANALYSIS (N={total_n})")
    typer.echo("═" * 67)
    typer.echo(f" {'Bucket':<14} {'N':>4} {'Wins':>5} {'Losses':>7} {'WR%':>7} {'Avg PnL':>11} {'Total':>11}")
    stats_per_bucket: dict[str, dict] = {}
    for label in ("with_trend", "counter_trend", "ranging", "unclassified"):
        s = _bucket_stats(buckets.get(label, []))
        stats_per_bucket[label] = s
        typer.echo(
            f" {label:<14} {s['n']:>4d} {s['wins']:>5d} {s['losses']:>7d} "
            f"{s['wr']:>6.1f}% {s['avg_pnl']:>+11.2f} {s['total_pnl']:>+11.2f}"
        )
    wt = stats_per_bucket["with_trend"]
    ct = stats_per_bucket["counter_trend"]
    if wt["n"] > 0 and ct["n"] > 0:
        delta = wt["wr"] - ct["wr"]
        typer.echo(f"\n WR delta (with_trend - counter_trend): {delta:+.1f} pp")
    return stats_per_bucket


# ----------------------------- main ----------------------------------------- #


@app.command()
def main(
    runs: Path = typer.Option(
        REPO_ROOT / "data" / "backtest",
        help="Backtest JSON path, or a directory (newest *.json is used).",
    ),
    lookback: int = typer.Option(20, help="HTF (4h) bars used for trend classification."),
) -> None:
    trades = _load_trades(runs)
    if not trades:
        typer.echo("No trades found.")
        raise typer.Exit(0)

    # Group by symbol
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_symbol[t.get("symbol", "UNKNOWN")].append(t)

    # Cache 1h Parquet per symbol
    parquet_cache: dict[str, pl.DataFrame] = {}

    # Per-symbol buckets
    overall_buckets: dict[str, list[dict]] = defaultdict(list)
    per_symbol_stats: list[tuple[str, dict]] = []

    for symbol, sym_trades in by_symbol.items():
        try:
            df_1h = parquet_cache.setdefault(symbol, _load_1h_parquet(symbol))
        except FileNotFoundError as e:
            typer.echo(f"⚠️  {symbol}: skipped ({e})")
            continue

        buckets: dict[str, list[dict]] = defaultdict(list)
        for t in sym_trades:
            label = _classify_trade(t, df_1h, lookback=lookback)
            if label is None:
                buckets["unclassified"].append(t)
            else:
                buckets[label].append(t)
            overall_buckets[label or "unclassified"].append(t)

        stats = _print_symbol_table(symbol, buckets)
        per_symbol_stats.append((symbol, stats))

    # ---------- overall table ---------- #
    typer.echo("\n" + "═" * 67)
    typer.echo(" OVERALL — all symbols pooled")
    typer.echo("═" * 67)
    typer.echo(f" {'Bucket':<14} {'N':>4} {'Wins':>5} {'Losses':>7} {'WR%':>7} {'Avg PnL':>11} {'Total':>11}")
    for label in ("with_trend", "counter_trend", "ranging", "unclassified"):
        s = _bucket_stats(overall_buckets.get(label, []))
        typer.echo(
            f" {label:<14} {s['n']:>4d} {s['wins']:>5d} {s['losses']:>7d} "
            f"{s['wr']:>6.1f}% {s['avg_pnl']:>+11.2f} {s['total_pnl']:>+11.2f}"
        )

    # ---------- counter-trend ratio per symbol ---------- #
    typer.echo("\n" + "═" * 67)
    typer.echo(" COUNTER-TREND EXPOSURE (per symbol)")
    typer.echo("═" * 67)
    typer.echo(f" {'Symbol':<10} {'N':>4} {'CT count':>9} {'CT ratio':>9} {'CT WR':>7} {'WT WR':>7} {'Delta':>8}")
    for symbol, stats in per_symbol_stats:
        n_total = sum(stats[b]["n"] for b in ("with_trend", "counter_trend", "ranging", "unclassified"))
        n_classified = stats["with_trend"]["n"] + stats["counter_trend"]["n"]
        ct_count = stats["counter_trend"]["n"]
        ct_ratio = (ct_count / n_classified * 100) if n_classified > 0 else 0.0
        ct_wr = stats["counter_trend"]["wr"]
        wt_wr = stats["with_trend"]["wr"]
        delta = wt_wr - ct_wr
        typer.echo(
            f" {symbol:<10} {n_total:>4d} {ct_count:>9d} {ct_ratio:>8.1f}% "
            f"{ct_wr:>6.1f}% {wt_wr:>6.1f}% {delta:>+7.1f}pp"
        )


if __name__ == "__main__":
    app()
