"""Bucket closed trades into UTC hour-of-day and weekday vs weekend.

Per symbol prints:
  - A 24-row hour histogram (skipping empty hours): N, WR%, total PnL, marker
  - A 2-row weekday vs weekend breakdown

Markers:
  ★ peak (highest positive total in this symbol)
  💀 worst (lowest negative total in this symbol)
  ●  any hour with at least one trade
  (blank for empty hours — those rows are suppressed in the printed table)

Usage:
    python -m scripts.analyze_trades_by_hour --runs data/backtest/multi_run10.json
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import typer

from src.config import REPO_ROOT

UTC = timezone.utc

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
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _bucket(trades: list[dict]) -> dict[str, float]:
    n = len(trades)
    if n == 0:
        return {"n": 0, "wins": 0, "losses": 0, "wr": 0.0, "total": 0.0}
    pnls = [float(t.get("pnl", 0.0)) for t in trades]
    return {
        "n": n,
        "wins": sum(1 for p in pnls if p > 0),
        "losses": sum(1 for p in pnls if p < 0),
        "wr": sum(1 for p in pnls if p > 0) / n * 100,
        "total": sum(pnls),
    }


def _hour_table(symbol: str, hour_trades: dict[int, list[dict]]) -> None:
    rows = []
    for h in range(24):
        b = _bucket(hour_trades.get(h, []))
        rows.append((h, b))
    nonempty = [r for r in rows if r[1]["n"] > 0]

    if not nonempty:
        typer.echo(f"\n {symbol}: no trades")
        return

    totals = [r[1]["total"] for r in nonempty]
    peak = max(totals)
    worst = min(totals)

    typer.echo("\n" + "═" * 56)
    typer.echo(f" {symbol} — HOUR-OF-DAY (UTC) — non-empty hours only")
    typer.echo("═" * 56)
    typer.echo(f" {'Hour':<5} {'N':>3} {'W':>3} {'L':>3} {'WR%':>6} {'Total':>10}   Marker")
    typer.echo("-" * 56)
    for h, b in nonempty:
        marker = "●"
        if b["total"] == peak and b["total"] > 0:
            marker = "★ peak"
        elif b["total"] == worst and b["total"] < 0:
            marker = "💀 worst"
        typer.echo(
            f" {h:02d}    {b['n']:>3d} {b['wins']:>3d} {b['losses']:>3d} "
            f"{b['wr']:>5.1f}% {b['total']:>+10.2f}   {marker}"
        )


def _weekday_table(symbol: str, day_trades: dict[bool, list[dict]]) -> None:
    weekday = _bucket(day_trades.get(False, []))
    weekend = _bucket(day_trades.get(True, []))
    typer.echo(f"\n {symbol} — WEEKDAY vs WEEKEND (UTC)")
    typer.echo("-" * 56)
    typer.echo(f" {'Day':<10} {'N':>3} {'W':>3} {'L':>3} {'WR%':>6} {'Total':>10}")
    typer.echo(
        f" {'weekday':<10} {weekday['n']:>3d} {weekday['wins']:>3d} {weekday['losses']:>3d} "
        f"{weekday['wr']:>5.1f}% {weekday['total']:>+10.2f}"
    )
    typer.echo(
        f" {'weekend':<10} {weekend['n']:>3d} {weekend['wins']:>3d} {weekend['losses']:>3d} "
        f"{weekend['wr']:>5.1f}% {weekend['total']:>+10.2f}"
    )


@app.command()
def main(
    runs: Path = typer.Option(
        REPO_ROOT / "data" / "backtest",
        help="Backtest JSON path or directory (newest *.json picked).",
    ),
    symbols: str | None = typer.Option(
        None, help="Comma-separated symbol filter; default = all symbols in the file.",
    ),
) -> None:
    trades = _load_trades(runs)
    if not trades:
        typer.echo("No trades.")
        raise typer.Exit(0)

    filter_set = {s.strip() for s in symbols.split(",")} if symbols else None

    by_symbol_hour: dict[str, dict[int, list[dict]]] = defaultdict(lambda: defaultdict(list))
    by_symbol_day: dict[str, dict[bool, list[dict]]] = defaultdict(lambda: defaultdict(list))
    overall_hour: dict[int, list[dict]] = defaultdict(list)
    overall_day: dict[bool, list[dict]] = defaultdict(list)

    for t in trades:
        sym = t.get("symbol", "UNKNOWN")
        if filter_set and sym not in filter_set:
            continue
        fill = _parse_ts(t.get("filled_ts")) or _parse_ts(t.get("opened_ts"))
        if fill is None:
            continue
        h = fill.hour
        is_weekend = fill.weekday() >= 5    # Mon=0 ... Sat=5, Sun=6
        by_symbol_hour[sym][h].append(t)
        by_symbol_day[sym][is_weekend].append(t)
        overall_hour[h].append(t)
        overall_day[is_weekend].append(t)

    for sym in sorted(by_symbol_hour.keys()):
        _hour_table(sym, by_symbol_hour[sym])
        _weekday_table(sym, by_symbol_day[sym])

    typer.echo("\n" + "═" * 56)
    typer.echo(" OVERALL — all symbols pooled")
    typer.echo("═" * 56)
    _hour_table("OVERALL", overall_hour)
    _weekday_table("OVERALL", overall_day)


if __name__ == "__main__":
    app()
