"""Backtest runner — replay historical Parquet OHLCV through the live engine.

CLI:
    python -m src.backtest.runner --symbol BTCUSDT --from 2025-11-01
    python -m src.backtest.runner --symbol BTCUSDT --from 2025-11-01 --to 2026-04-01 \
        --no-killzone --output data/backtest/btc_run1.json

Use the same `sweep_fvg.evaluate` + `PaperBroker` + `PositionManager` as live;
no Telegram or scheduler involvement. State writes are disabled (`persist=False`)
so backtests don't pollute the live broker snapshot.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import typer

from src.analysis.trend_classifier import aggregate_to_4h
from src.backtest.metrics import BacktestMetrics, compute_metrics, format_metrics
from src.config import REPO_ROOT, get_env, get_settings, get_strategy_params
from src.engine.paper_broker import CloseReason, PaperBroker
from src.engine.position_mgr import PositionManager
from src.engine.signal_router import SignalRouter
from src.filters.regime_filter import is_regime_tradeable
from src.strategies import ote as _ote_mod
from src.strategies import sweep_fvg as _sweep_mod
from src.strategies.sweep_fvg import TradeSignal

# Strategy registry: name -> (evaluate_fn, SetupParams class)
STRATEGY_REGISTRY = {
    "sweep_fvg": (_sweep_mod.evaluate, _sweep_mod.SetupParams),
    "ote":       (_ote_mod.evaluate,   _ote_mod.SetupParams),
}
from src.utils.logging import logger, setup_logging
from src.utils.rr_metrics import calculate_expectancy, format_expectancy
from src.utils.time import timeframe_to_seconds

UTC = timezone.utc

# Same warmup threshold as live so the comparison is apples-to-apples.
MIN_BARS_FOR_STRATEGY = 60

# Sliding window for strategy evaluation. ICT primitives only need the recent
# past (swings, FVGs, pools) — passing the full multi-month DataFrame to
# `evaluate()` every bar is O(n²) on the bar count. Window must be large enough
# to cover ATR(14) warmup + a few swings + range_lookback × HTF context.
WINDOW_LTF_BARS = 400      # ~33 hours of 5m
WINDOW_HTF_BARS = 300      # ~12.5 days of 1h — needs to be big enough to host
                            # swings + BOS event so HTF trend isn't perpetually NEUTRAL

app = typer.Typer(help="Backtest the strategy on historical OHLCV data.")


# ----------------------------- helpers -------------------------------------- #


def _load_parquet(symbol: str, tf: str) -> pl.DataFrame:
    env = get_env()
    path = REPO_ROOT / env.ohlcv_data_dir / f"{symbol}_{tf}.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"OHLCV not found: {path}. Run downloader first:\n"
            f"  python -m src.data.downloader download --symbol {symbol} --tf {tf} --from 2025-11-01"
        )
    return pl.read_parquet(path).sort("ts")


def _filter_range(df: pl.DataFrame, from_dt: datetime, to_dt: datetime) -> pl.DataFrame:
    return df.filter((pl.col("ts") >= from_dt) & (pl.col("ts") < to_dt))


def _build_htf_lookup(df_ltf: pl.DataFrame, df_htf: pl.DataFrame, htf_tf: str) -> list[int]:
    """For each LTF bar i, return index of latest HTF bar that has fully closed by ltf_ts.

    Returns -1 for LTF bars where no HTF bar has yet closed.
    """
    htf_secs = timeframe_to_seconds(htf_tf)
    htf_ts_secs = [int(t.timestamp()) for t in df_htf["ts"].to_list()]
    ltf_ts_secs = [int(t.timestamp()) for t in df_ltf["ts"].to_list()]
    result: list[int] = []
    j = -1
    for ts in ltf_ts_secs:
        # Advance j while the *next* HTF bar's close is also <= current LTF ts
        while j + 1 < len(htf_ts_secs) and htf_ts_secs[j + 1] + htf_secs <= ts:
            j += 1
        result.append(j)
    return result


# ----------------------------- core ----------------------------------------- #


async def run_backtest(
    *,
    symbol: str,
    from_dt: datetime,
    to_dt: datetime,
    entry_tf: str = "5m",
    bias_tf: str = "1h",
    params=None,
    initial_equity: float = 10_000.0,
    strategy: str = "sweep_fvg",
) -> tuple[BacktestMetrics, PaperBroker]:
    if strategy not in STRATEGY_REGISTRY:
        raise ValueError(
            f"Unknown strategy {strategy!r}. Available: {list(STRATEGY_REGISTRY)}"
        )
    evaluate_strategy, ParamsCls = STRATEGY_REGISTRY[strategy]

    df_ltf = _filter_range(_load_parquet(symbol, entry_tf), from_dt, to_dt)
    df_htf = _filter_range(_load_parquet(symbol, bias_tf), from_dt, to_dt)
    if df_ltf.height < MIN_BARS_FOR_STRATEGY or df_htf.height < MIN_BARS_FOR_STRATEGY:
        raise ValueError(
            f"Not enough data after filtering | ltf={df_ltf.height} htf={df_htf.height} "
            f"(need >={MIN_BARS_FOR_STRATEGY}). Widen the date range or download more history."
        )

    if params is None:
        params = ParamsCls.from_strategy_params(get_strategy_params())

    settings = get_settings()
    # Use a tmp snapshot so test runs / multiple backtests don't clash
    tmp_dir = Path(tempfile.mkdtemp(prefix="kripto_bt_"))
    broker = PaperBroker(
        initial_equity=initial_equity,
        risk_per_trade_pct=get_env().paper_risk_per_trade,
        snapshot_path=tmp_dir / "state.json",
        persist=False,
    )
    position_mgr = PositionManager(broker=broker)

    # Route signals through the same SignalRouter as live so cooldown / dedup /
    # duplicate / max_concurrent filters apply. Provider callbacks let the
    # router query live broker state — never drifts on close.
    def _active_positions():
        return [*broker.pending_positions, *broker.open_positions]

    # Regime gate: aggregate the (1h) HTF df to 4h once, then per-signal look up
    # the latest 4h bar whose close is <= signal.ts and classify the trailing
    # window. Strict look-ahead guard: we filter on `ts <= signal.ts` before
    # calling the classifier.
    regime_cfg = settings.filters.regime_filter
    regime_check = None
    if regime_cfg.enabled and regime_cfg.htf_timeframe == "4h":
        df_4h_full = aggregate_to_4h(df_htf)

        def _regime_check(sig: TradeSignal) -> tuple[bool, str]:
            df_past = df_4h_full.filter(pl.col("ts") <= sig.ts)
            return is_regime_tradeable(
                df_past,
                exclude_ranging=regime_cfg.exclude_ranging,
                exclude_trending=regime_cfg.exclude_trending,
                classification_lookback=regime_cfg.classification_lookback,
            )
        regime_check = _regime_check
    elif regime_cfg.enabled and regime_cfg.htf_timeframe == "1h":
        # When the regime timeframe equals the bias timeframe, no aggregation.
        def _regime_check(sig: TradeSignal) -> tuple[bool, str]:
            df_past = df_htf.filter(pl.col("ts") <= sig.ts)
            return is_regime_tradeable(
                df_past,
                exclude_ranging=regime_cfg.exclude_ranging,
                exclude_trending=regime_cfg.exclude_trending,
                classification_lookback=regime_cfg.classification_lookback,
            )
        regime_check = _regime_check

    router = SignalRouter(
        cooldown_minutes=settings.filters.cooldown_minutes,
        max_concurrent=settings.paper.max_concurrent_positions,
        active_count_provider=broker.total_active,
        active_positions_provider=_active_positions,
        regime_check=regime_check,
    )

    async def _paper_handler(signal: TradeSignal) -> None:
        broker.open_from_signal(signal)

    router.add_handler(_paper_handler)

    htf_lookup = _build_htf_lookup(df_ltf, df_htf, bias_tf)

    logger.info(
        "Backtest start | {} | {} bars LTF, {} bars HTF | range {}..{}",
        symbol, df_ltf.height, df_htf.height,
        df_ltf['ts'][0], df_ltf['ts'][-1],
    )

    n_signals = 0
    n_filled = 0
    progress_step = max(1, df_ltf.height // 20)   # ~5% increments

    for i in range(df_ltf.height):
        bar_ts = df_ltf["ts"][i]
        bar_high = float(df_ltf["high"][i])
        bar_low = float(df_ltf["low"][i])

        # 1) Position management on this bar
        pre_open = len(broker.open_positions)
        await position_mgr.on_bar(symbol, bar_high, bar_low, bar_ts)
        n_filled += max(0, len(broker.open_positions) - pre_open)

        # 2) Skip warmup bars
        if i < MIN_BARS_FOR_STRATEGY:
            continue
        htf_idx = htf_lookup[i]
        if htf_idx < MIN_BARS_FOR_STRATEGY:
            continue

        # 3) Strategy evaluation on a sliding window — keeps cost O(n × window)
        ltf_start = max(0, i - WINDOW_LTF_BARS + 1)
        df_ltf_w = df_ltf.slice(ltf_start, i - ltf_start + 1)
        local_ltf_idx = i - ltf_start

        htf_start = max(0, htf_idx - WINDOW_HTF_BARS + 1)
        df_htf_w = df_htf.slice(htf_start, htf_idx - htf_start + 1)
        local_htf_idx = htf_idx - htf_start

        signal = evaluate_strategy(
            symbol=symbol,
            df_ltf=df_ltf_w,
            df_htf=df_htf_w,
            ltf_bar_index=local_ltf_idx,
            htf_bar_index=local_htf_idx,
            params=params,
        )
        if signal is None:
            if i % progress_step == 0 and i > 0:
                pct = (i / df_ltf.height) * 100
                logger.info(
                    "Backtest progress | {:.0f}% | bar {}/{} | signals={} | equity={:.2f}",
                    pct, i, df_ltf.height, n_signals, broker.equity,
                )
            continue

        # Re-anchor signal's bar_index back to the global df_ltf (window-local
        # → global). The signal's bar_index is used by SignalRouter for dedup
        # and by Position.id, so it must be consistent across the run.
        signal = _rewrite_signal_global_index(signal, global_index=i,
                                              global_ts=bar_ts,
                                              meta_ts_offset=ltf_start)
        n_signals += 1
        await router.submit(signal)

    # 4) Close any still-open positions at last close (mark-to-end)
    if df_ltf.height > 0:
        last_close = float(df_ltf["close"][-1])
        last_ts = df_ltf["ts"][-1]
        for pos in list(broker.open_positions):
            broker.close_position(pos, last_close, CloseReason.EXPIRED, last_ts)

    metrics = compute_metrics(broker.closed_trades, initial_equity)
    logger.info(
        "Backtest done | signals={} filled={} closed={}",
        n_signals, n_filled, len(broker.closed_trades),
    )
    return metrics, broker


# ----------------------------- CLI ------------------------------------------ #


def _parse_iso_date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


@app.command()
def run(
    symbol: str = typer.Option(..., help="e.g. BTCUSDT"),
    from_: str = typer.Option(..., "--from", help="ISO date YYYY-MM-DD (UTC)"),
    to: str | None = typer.Option(None, help="ISO date YYYY-MM-DD (default: now UTC)"),
    entry_tf: str = typer.Option("5m"),
    bias_tf: str = typer.Option("1h"),
    initial_equity: float = typer.Option(10_000.0),
    no_killzone: bool = typer.Option(False, help="Disable killzone filter (longer evaluation window)"),
    strategy: str = typer.Option("sweep_fvg", help=f"Strategy to evaluate. One of: {list(STRATEGY_REGISTRY)}"),
    output: Path | None = typer.Option(None, help="Optional JSON output path for metrics + trades"),
) -> None:
    setup_logging()
    settings = get_settings()
    from_dt = _parse_iso_date(from_)
    to_dt = _parse_iso_date(to) if to else datetime.now(tz=UTC)

    if strategy not in STRATEGY_REGISTRY:
        raise typer.BadParameter(
            f"Unknown strategy {strategy!r}. Choose from {list(STRATEGY_REGISTRY)}"
        )
    _, ParamsCls = STRATEGY_REGISTRY[strategy]
    params = ParamsCls.from_strategy_params(get_strategy_params())
    if no_killzone:
        params.require_killzone = False

    metrics, broker = asyncio.run(run_backtest(
        symbol=symbol, from_dt=from_dt, to_dt=to_dt,
        entry_tf=entry_tf, bias_tf=bias_tf,
        params=params, initial_equity=initial_equity,
        strategy=strategy,
    ))

    typer.echo(format_metrics(metrics))

    trade_dicts = [_trade_to_jsonable(t) for t in broker.closed_trades]
    expectancy = calculate_expectancy(
        trade_dicts,
        maker_rate=settings.paper.fee_maker,
        taker_rate=settings.paper.fee_taker,
    )
    typer.echo("\n" + format_expectancy(expectancy))

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "symbol": symbol,
            "strategy": strategy,
            "from": from_dt.isoformat(),
            "to": to_dt.isoformat(),
            "entry_tf": entry_tf,
            "bias_tf": bias_tf,
            "params": asdict(params),
            "metrics": _metrics_to_jsonable(metrics),
            "expectancy": expectancy,
            "trades": trade_dicts,
        }
        output.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        typer.echo(f"\nWrote {len(broker.closed_trades)} trades + metrics to {output}")


def _rewrite_signal_global_index(
    signal,                    # type: TradeSignal
    *,
    global_index: int,
    global_ts,                 # type: datetime
    meta_ts_offset: int,
):
    """Translate a window-local TradeSignal back to global LTF indices.

    The strategy receives a sliced DataFrame and emits indices relative to that
    slice; for downstream uniqueness (Position.id, SignalRouter dedup) we want
    indices relative to the full backtest df_ltf.
    """
    from dataclasses import replace
    new_meta = dict(signal.meta or {})
    if "sweep_bar_index" in new_meta and isinstance(new_meta["sweep_bar_index"], int):
        new_meta["sweep_bar_index"] += meta_ts_offset
    if "mss_bar_index" in new_meta and isinstance(new_meta["mss_bar_index"], int):
        new_meta["mss_bar_index"] += meta_ts_offset
    if "fvg_middle_index" in new_meta and isinstance(new_meta["fvg_middle_index"], int):
        new_meta["fvg_middle_index"] += meta_ts_offset
    return replace(signal, bar_index=global_index, ts=global_ts, meta=new_meta)


def _metrics_to_jsonable(m: BacktestMetrics) -> dict:
    d = asdict(m)
    d["equity_curve"] = [
        {"ts": pt.ts.isoformat(), "equity": pt.equity} for pt in m.equity_curve
    ]
    return d


def trade_to_dict(t) -> dict:
    """Public wrapper kept for callers outside the runner (e.g. multi-symbol script)."""
    return _trade_to_jsonable(t)


def _trade_to_jsonable(t) -> dict:
    p = t.position
    return {
        "id": p.id,
        "symbol": p.symbol,
        "side": p.side.value,
        "fill_entry": p.fill_entry,
        "exit_price": p.exit_price,
        "stop_loss": p.stop_loss,
        "take_profit": p.take_profit,
        "size": p.size,
        "pnl": p.pnl,
        "close_reason": p.close_reason.value if p.close_reason else None,
        "opened_ts": p.opened_ts.isoformat() if p.opened_ts else None,
        "filled_ts": p.filled_ts.isoformat() if p.filled_ts else None,
        "closed_ts": p.closed_ts.isoformat() if p.closed_ts else None,
        "rr_target": t.position.meta.get("signal_rr"),
    }


if __name__ == "__main__":
    app()
