"""Classify closed trades by which killzone was active at fill time.

Buckets (all in UTC):
- london_only:  inside London (07:00-10:00) but not NY
- ny_only:      inside NY (12:00-15:00) but not London
- both_active:  inside the London/NY overlap (13:00-15:00 UTC)
- outside:      neither (only meaningful when killzone filter is OFF)

When the backtest was run with the killzone filter active (run11), the
'outside' bucket should be empty — its presence indicates a leak.

Usage:
    python -m scripts.analyze_trades_by_killzone --runs data/backtest/multi_run11.json
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, time, timezone
from pathlib import Path

import typer

from src.config import REPO_ROOT

UTC = timezone.utc

# Hard-coded UTC windows — analysis-only, not driven by config. Keeps the
# script self-contained: looking at past trades regardless of how the bot was
# configured when they were taken.
LONDON_START = time(7, 0)
LONDON_END = time(10, 0)
NY_START = time(12, 0)
NY_END = time(15, 0)

app = typer.Typer(add_completion=False)


def _load_trades(runs_path: Path) -> list[dict]:
    if runs_path.is_dir():
        files = sorted(runs_path.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            raise FileNotFoundError(f"No JSON files in {runs_path}")
        runs_path = files[0]
        typer.echo(f"Using newest backtest file: {runs_path.name}\n")

    payload = json.loads(runs_path.read_text(encoding="utf-8"))

    if "per_symbol" in payload:
        out: list[dict] = []
        for row in payload["per_symbol"]:
            for t in row.get("trades", []):
                t = dict(t)
                t.setdefault("symbol", row.get("symbol"))
                out.append(t)
        return out

    if "trades" in payload:
        sym = payload.get("symbol", "UNKNOWN")
        return [{**t, "symbol": t.get("symbol", sym)} for t in payload["trades"]]

    raise ValueError(f"Unrecognised backtest JSON shape: {runs_path}")


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _classify(t_utc: time) -> str:
    in_london = LONDON_START <= t_utc < LONDON_END
    in_ny = NY_START <= t_utc < NY_END
    overlap_start = max(LONDON_START, NY_START)
    overlap_end = min(LONDON_END, NY_END)
    in_overlap = (overlap_start < overlap_end) and (overlap_start <= t_utc < overlap_end)
    if in_overlap:
        return "both_active"
    if in_london:
        return "london_only"
    if in_ny:
        return "ny_only"
    return "outside"


def _bucket_row(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {"n": 0, "wins": 0, "losses": 0, "wr": 0.0, "avg_pnl": 0.0, "total": 0.0}
    pnls = [float(t.get("pnl", 0.0)) for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "wr": (wins / n) * 100,
        "avg_pnl": sum(pnls) / n,
        "total": sum(pnls),
    }


def _print_table(title: str, buckets: dict[str, list[dict]]) -> None:
    total_n = sum(len(v) for v in buckets.values())
    typer.echo("\n" + "═" * 67)
    typer.echo(f" {title} (N={total_n})")
    typer.echo("═" * 67)
    typer.echo(f" {'Bucket':<14} {'N':>4} {'Wins':>5} {'Losses':>7} {'WR%':>7} {'Avg PnL':>11} {'Total':>11}")
    for label in ("london_only", "ny_only", "both_active", "outside"):
        b = _bucket_row(buckets.get(label, []))
        typer.echo(
            f" {label:<14} {b['n']:>4d} {b['wins']:>5d} {b['losses']:>7d} "
            f"{b['wr']:>6.1f}% {b['avg_pnl']:>+11.2f} {b['total']:>+11.2f}"
        )


@app.command()
def main(
    runs: Path = typer.Option(
        REPO_ROOT / "data" / "backtest",
        help="Backtest JSON path or directory (newest *.json picked).",
    ),
) -> None:
    trades = _load_trades(runs)
    if not trades:
        typer.echo("No trades.")
        raise typer.Exit(0)

    by_symbol: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    overall: dict[str, list[dict]] = defaultdict(list)

    for t in trades:
        fill = _parse_ts(t.get("filled_ts")) or _parse_ts(t.get("opened_ts"))
        if fill is None:
            continue
        label = _classify(fill.time())
        sym = t.get("symbol", "UNKNOWN")
        by_symbol[sym][label].append(t)
        overall[label].append(t)

    for sym, buckets in sorted(by_symbol.items()):
        _print_table(f"{sym} — KILLZONE BUCKETS", buckets)

    _print_table("OVERALL — all symbols pooled", overall)


if __name__ == "__main__":
    app()
