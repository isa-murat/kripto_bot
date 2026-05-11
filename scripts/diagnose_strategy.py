"""Diagnose ICT primitives on real Parquet data.

Reports raw counts of swings / FVGs / pools / events on the full df,
then runs a streaming funnel for sweep_fvg.evaluate() over the bars and
tells you exactly which filter killed each potential signal.

Usage:
    python -m scripts.diagnose_strategy --symbol BTCUSDT
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import typer

from src.config import REPO_ROOT, get_env, get_strategy_params
from src.ict.bias import compute_bias
from src.ict.killzone import is_active_killzone
from src.ict.liquidity import (
    PoolType,
    active_pools_at,
    detect_sweep,
    find_pools,
)
from src.ict.poi import find_fvgs, is_fvg_mitigated
from src.ict.structure import (
    EventType,
    SwingType,
    Trend,
    compute_atr,
    detect_events,
    find_swings,
)
from src.strategies.sweep_fvg import SetupParams

app = typer.Typer(add_completion=False)


@app.command()
def main(
    symbol: str = typer.Option("BTCUSDT"),
    entry_tf: str = typer.Option("5m"),
    bias_tf: str = typer.Option("1h"),
    no_killzone: bool = typer.Option(True),
    sample_bars: int = typer.Option(5000, help="Funnel sample size (0=all)"),
) -> None:
    env = get_env()
    df_ltf = pl.read_parquet(REPO_ROOT / env.ohlcv_data_dir / f"{symbol}_{entry_tf}.parquet").sort("ts")
    df_htf = pl.read_parquet(REPO_ROOT / env.ohlcv_data_dir / f"{symbol}_{bias_tf}.parquet").sort("ts")

    print("=" * 70)
    print(f"Symbol: {symbol}  |  LTF {entry_tf}: {df_ltf.height} bars  |  HTF {bias_tf}: {df_htf.height}")
    print("=" * 70)

    # ----- Raw primitives over the full LTF dataset --------------------- #
    atr = compute_atr(df_ltf, 14)
    median_atr = float(atr.drop_nulls().median() or 0.0)
    print(f"\nLTF ATR(14): median={median_atr:.2f}")

    swings = find_swings(df_ltf, lookback=3)
    highs = [s for s in swings if s.type == SwingType.HIGH]
    lows = [s for s in swings if s.type == SwingType.LOW]
    print(f"Swings:  total={len(swings)}  highs={len(highs)}  lows={len(lows)}")

    events = detect_events(df_ltf, swings, atr=atr, displacement_atr_mult=1.5)
    bos = [e for e in events if e.type == EventType.BOS]
    choch = [e for e in events if e.type == EventType.CHOCH]
    mss = [e for e in events if e.type == EventType.MSS]
    print(f"Events:  total={len(events)}  BOS={len(bos)}  CHoCH={len(choch)}  MSS={len(mss)}")

    fvgs = find_fvgs(df_ltf, atr=atr, min_atr_mult=0.15)
    fvg_bull = [f for f in fvgs if f.direction == Trend.BULL]
    fvg_bear = [f for f in fvgs if f.direction == Trend.BEAR]
    print(f"FVGs:    total={len(fvgs)}  bull={len(fvg_bull)}  bear={len(fvg_bear)}")

    pool_tol = 0.10 * median_atr
    pools = find_pools(swings, tolerance_price=pool_tol, min_count=1)
    bsl = [p for p in pools if p.type == PoolType.BSL]
    ssl = [p for p in pools if p.type == PoolType.SSL]
    print(f"Pools:   total={len(pools)}  BSL={len(bsl)}  SSL={len(ssl)}  (tolerance={pool_tol:.2f})")

    # ----- Streaming funnel over bars ---------------------------------- #
    print("\n" + "=" * 70)
    print("FUNNEL — bars iterated sequentially, count of bars rejected at each step")
    print("=" * 70)

    params = SetupParams.from_strategy_params(get_strategy_params())
    if no_killzone:
        params.require_killzone = False

    funnel = {
        "bars_evaluated": 0,
        "bias_neutral": 0,
        "no_killzone": 0,
        "no_atr": 0,
        "no_target_pool": 0,
        "no_sweep": 0,
        "no_mss": 0,
        "no_fvg": 0,
        "fvg_mitigated": 0,
        "no_tp_pool": 0,
        "min_rr_failed": 0,
        "fee_filter_failed": 0,
        "min_sl_distance_kicked_in": 0,
        "signal_emitted": 0,
    }

    # Build HTF lookup once
    from src.utils.time import timeframe_to_seconds
    htf_secs = timeframe_to_seconds(bias_tf)
    htf_ts_secs = [int(t.timestamp()) for t in df_htf["ts"].to_list()]
    ltf_ts_secs = [int(t.timestamp()) for t in df_ltf["ts"].to_list()]
    htf_lookup: list[int] = []
    j = -1
    for ts in ltf_ts_secs:
        while j + 1 < len(htf_ts_secs) and htf_ts_secs[j + 1] + htf_secs <= ts:
            j += 1
        htf_lookup.append(j)

    WINDOW_LTF = 400
    WINDOW_HTF = 300

    end_bar = df_ltf.height if sample_bars <= 0 else min(60 + sample_bars, df_ltf.height)
    for i in range(60, end_bar):
        htf_idx = htf_lookup[i]
        if htf_idx < 60:
            continue
        funnel["bars_evaluated"] += 1

        # Window slice
        ltf_start = max(0, i - WINDOW_LTF + 1)
        df_l = df_ltf.slice(ltf_start, i - ltf_start + 1)
        local_i = i - ltf_start
        htf_start = max(0, htf_idx - WINDOW_HTF + 1)
        df_h = df_htf.slice(htf_start, htf_idx - htf_start + 1)
        local_h = htf_idx - htf_start

        # Replicate evaluate() steps but count rejections
        bias = compute_bias(
            df_h, local_h,
            swing_lookback=params.htf_swing_lookback,
            range_lookback_swings=params.htf_range_lookback_swings,
            premium_threshold=params.htf_premium_threshold,
            discount_threshold=params.htf_discount_threshold,
            displacement_atr_mult=params.displacement_atr_mult,
        )
        # Match evaluate(): trend-only mode if zone not required
        effective_bias = bias.bias if params.bias_zone_required else bias.trend
        if effective_bias == Trend.NEUTRAL:
            funnel["bias_neutral"] += 1
            continue
        if params.require_killzone and not is_active_killzone(df_l["ts"][local_i]):
            funnel["no_killzone"] += 1
            continue

        ltf_swings = find_swings(df_l, lookback=params.swing_lookback)
        ltf_atr = compute_atr(df_l)
        cur_atr = float(ltf_atr[local_i] or 0.0)
        if cur_atr <= 0:
            funnel["no_atr"] += 1
            continue

        pool_tol = params.equal_level_tolerance_atr * cur_atr
        all_pools = find_pools(ltf_swings, tolerance_price=pool_tol, min_count=1)
        pools_now = active_pools_at(all_pools, local_i)
        target_type = PoolType.SSL if effective_bias == Trend.BULL else PoolType.BSL
        target_pools = [p for p in pools_now if p.type == target_type]
        if not target_pools:
            funnel["no_target_pool"] += 1
            continue

        # Match evaluate(): scan backwards for the most recent sweep
        sweep = None
        lb_start = max(0, local_i - params.sweep_lookback_bars)
        for k in range(local_i, lb_start - 1, -1):
            s = detect_sweep(df_l, k, target_pools, params.sweep_min_wick_pct)
            if s is not None:
                sweep = s
                break
        if sweep is None:
            funnel["no_sweep"] += 1
            continue

        events = detect_events(df_l, ltf_swings, atr=ltf_atr,
                               displacement_atr_mult=params.displacement_atr_mult)
        aligned = [
            e for e in events
            if e.direction == effective_bias
            and e.type in (EventType.MSS, EventType.CHOCH, EventType.BOS)
            and sweep.bar_index <= e.index <= local_i
            and (e.index - sweep.bar_index) <= params.sweep_to_mss_max_bars
        ]
        if not aligned:
            funnel["no_mss"] += 1
            continue
        mss_e = aligned[-1]

        fvgs_w = find_fvgs(df_l, atr=ltf_atr, min_atr_mult=params.fvg_min_atr_mult)
        cands = [
            f for f in fvgs_w
            if f.direction == effective_bias
            and (mss_e.index - 1) <= f.middle_index <= (mss_e.index + params.mss_to_fvg_max_bars)
            and f.confirmed_at_index <= local_i
            and (local_i - f.middle_index) <= params.fvg_max_age_bars
        ]
        if not cands:
            funnel["no_fvg"] += 1
            continue
        fvg = cands[-1]
        if is_fvg_mitigated(df_l, fvg, until_index=local_i - 1):
            funnel["fvg_mitigated"] += 1
            continue

        entry = fvg.midpoint
        min_sl_dist = params.min_sl_distance_atr * cur_atr
        if effective_bias == Trend.BULL:
            sweep_low = float(df_l["low"][sweep.bar_index])
            raw_sl = sweep_low - params.sl_buffer_atr * cur_atr
            sl = min(raw_sl, entry - min_sl_dist)
            if abs(raw_sl - sl) > 1e-9:
                funnel["min_sl_distance_kicked_in"] += 1
            tps = [p for p in pools_now if p.type == PoolType.BSL and p.price > entry]
            if not tps:
                funnel["no_tp_pool"] += 1
                continue
            tp = min(p.price for p in tps)
        else:
            sweep_high = float(df_l["high"][sweep.bar_index])
            raw_sl = sweep_high + params.sl_buffer_atr * cur_atr
            sl = max(raw_sl, entry + min_sl_dist)
            if abs(raw_sl - sl) > 1e-9:
                funnel["min_sl_distance_kicked_in"] += 1
            tps = [p for p in pools_now if p.type == PoolType.SSL and p.price < entry]
            if not tps:
                funnel["no_tp_pool"] += 1
                continue
            tp = max(p.price for p in tps)

        risk = abs(entry - sl)
        reward = abs(tp - entry)
        if risk <= 0 or reward <= 0:
            funnel["min_rr_failed"] += 1
            continue
        rr = reward / risk
        if rr < params.min_rr:
            funnel["min_rr_failed"] += 1
            continue
        fee_per_unit = entry * params.fee_rate_round_trip
        if (fee_per_unit / reward) > params.max_fee_to_reward_ratio:
            funnel["fee_filter_failed"] += 1
            continue

        funnel["signal_emitted"] += 1

    print(f"\n{'metric':<32}{'count':>10}")
    print("-" * 42)
    for k, v in funnel.items():
        pct = (v / max(funnel["bars_evaluated"], 1)) * 100
        print(f"  {k:<30}{v:>10}  ({pct:5.1f}%)")
    print()


if __name__ == "__main__":
    app()
