"""Liquidity Sweep + FVG Entry strategy (MVP setup).

Pipeline (per LTF bar close):
    1. HTF bias  → BULL or BEAR (NEUTRAL → no signal)
    2. Killzone  → must be inside an active session
    3. Liquidity → bias-aligned pool was just swept (BULL bias hunts SSL,
                   BEAR bias hunts BSL)
    4. MSS       → structure shift in the bias direction within
                   `sweep_to_mss_max_bars` of the sweep
    5. FVG       → bias-aligned, fresh imbalance inside the displacement
                   window (`mss_to_fvg_max_bars`), not yet mitigated
    6. Entry     → FVG midpoint (limit)
       SL        → sweep wick + `sl_buffer_atr × ATR`
       TP        → nearest opposite-side liquidity, must satisfy `min_rr`

The function is pure: takes data + parameters, returns a `TradeSignal | None`.
Side effects (notifications, paper trades) live in `engine.signal_router`
and downstream modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import polars as pl

from src.config import StrategyParams
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
    Trend,
    compute_atr,
    detect_events,
    find_swings,
)


@dataclass(frozen=True)
class TradeSignal:
    symbol: str
    side: Trend                       # BULL = LONG, BEAR = SHORT
    entry_price: float                # FVG midpoint (limit order)
    stop_loss: float
    take_profit: float
    rr: float                         # reward/risk ratio
    setup_name: str
    bar_index: int                    # LTF bar index where signal fired
    ts: datetime                      # LTF bar open_time (UTC)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class SetupParams:
    swing_lookback: int = 3
    displacement_atr_mult: float = 1.5
    equal_level_tolerance_atr: float = 0.30
    sweep_min_wick_pct: float = 0.20
    sweep_lookback_bars: int = 30        # how far back to scan for the sweep
    sweep_to_mss_max_bars: int = 20
    mss_to_fvg_max_bars: int = 5
    fvg_min_atr_mult: float = 0.15
    fvg_max_age_bars: int = 30
    # ATR-multiple buffer past the sweep wick. Wider = more "room" before SL
    # gets liquidity-swept; narrower = better RR but more false stops.
    sl_buffer_atr: float = 0.30
    # Min SL distance (en sıkışık SL bile bu kadar uzakta olsun) — fee/leverage
    # patlamasını engellemek için kritik. Tatmin edemeyen setup atlanır.
    min_sl_distance_atr: float = 0.50
    # Min reward/risk. TP pool must be at least this many R away. Pools closer
    # than this are skipped (not rejected — we look further out).
    min_rr: float = 2.0
    # Max reward/risk. Pools further than this are ignored. Bounds the search
    # so the TP isn't dragged to a distant "major" liquidity pool that price
    # never reaches before SL — observed cause of low WR in run10.
    max_rr: float = 4.0
    # Skip the setup if estimated round-trip fee exceeds this fraction of the
    # planned reward. 0.30 = "fee can eat at most 30% of TP reward".
    max_fee_to_reward_ratio: float = 0.30
    # Pool quality filters (raise to demand stronger magnets).
    # `min_touches=1` lets every swing become a pool (legacy default);
    # `min_touches=2` keeps only equal-H/L clusters. `min_age_bars` drops
    # just-formed pools that haven't accumulated stops yet.
    pool_min_touches: int = 2
    pool_min_age_bars: int = 10
    # Round-trip fee rate used for the filter above. Must match broker's fee.
    fee_rate_round_trip: float = 0.0008
    # HTF (bias) tuning
    htf_swing_lookback: int = 5
    htf_range_lookback_swings: int = 4
    htf_premium_threshold: float = 0.50
    htf_discount_threshold: float = 0.50
    # Killzone toggle (False → ignore session filter, useful for backtest)
    require_killzone: bool = True
    # If False, only the HTF trend is used for bias (zone is ignored).
    # Use trend-only mode (False) when zone-strict bias produces too few signals
    # — empirically necessary on volatile crypto where price spends much time
    # outside discount/premium relative to a 4-swing range.
    bias_zone_required: bool = False

    @classmethod
    def from_strategy_params(cls, sp: StrategyParams) -> "SetupParams":
        s = sp.structure or {}
        l = sp.liquidity or {}
        p = sp.poi or {}
        b = sp.bias or {}
        f = sp.setup_sweep_fvg or {}
        return cls(
            swing_lookback=int(s.get("swing_lookback_5m", 3)),
            displacement_atr_mult=float(s.get("displacement_atr_mult", 1.5)),
            equal_level_tolerance_atr=float(l.get("equal_level_tolerance_atr", 0.10)),
            sweep_min_wick_pct=float(l.get("sweep_min_wick_pct", 0.30)),
            sweep_lookback_bars=int(f.get("sweep_lookback_bars", 30)),
            sweep_to_mss_max_bars=int(f.get("sweep_to_mss_max_bars", 20)),
            mss_to_fvg_max_bars=int(f.get("mss_to_fvg_max_bars", 5)),
            fvg_min_atr_mult=float(p.get("fvg_min_atr_mult", 0.15)),
            fvg_max_age_bars=int(p.get("fvg_max_age_bars", 30)),
            sl_buffer_atr=float(f.get("sl_buffer_atr", 0.30)),
            min_sl_distance_atr=float(f.get("min_sl_distance_atr", 0.50)),
            max_fee_to_reward_ratio=float(f.get("max_fee_to_reward_ratio", 0.30)),
            fee_rate_round_trip=float(f.get("fee_rate_round_trip", 0.0008)),
            bias_zone_required=bool(f.get("bias_zone_required", False)),
            min_rr=float(f.get("min_rr", 2.0)),
            max_rr=float(f.get("max_rr", 4.0)),
            pool_min_touches=int(l.get("equal_min_count", 2)),
            pool_min_age_bars=int(l.get("pool_min_age_bars", 10)),
            htf_swing_lookback=int(s.get("swing_lookback_1h", 5)),
            htf_range_lookback_swings=int(b.get("range_lookback_swings", 4)),
            htf_premium_threshold=float(b.get("premium_threshold", 0.50)),
            htf_discount_threshold=float(b.get("discount_threshold", 0.50)),
        )


SETUP_NAME = "sweep_fvg"


def evaluate(
    *,
    symbol: str,
    df_ltf: pl.DataFrame,
    df_htf: pl.DataFrame,
    ltf_bar_index: int | None = None,
    htf_bar_index: int | None = None,
    params: SetupParams | None = None,
) -> TradeSignal | None:
    """Evaluate the Sweep+FVG setup at `ltf_bar_index`. Returns a signal or None.

    Defaults: latest bar of each frame; default SetupParams.
    """
    if df_ltf.height == 0 or df_htf.height == 0:
        return None
    p = params or SetupParams()
    if ltf_bar_index is None:
        ltf_bar_index = df_ltf.height - 1
    if htf_bar_index is None:
        htf_bar_index = df_htf.height - 1

    # ---- 1. HTF bias ---------------------------------------------------- #
    bias = compute_bias(
        df_htf, htf_bar_index,
        swing_lookback=p.htf_swing_lookback,
        range_lookback_swings=p.htf_range_lookback_swings,
        premium_threshold=p.htf_premium_threshold,
        discount_threshold=p.htf_discount_threshold,
        displacement_atr_mult=p.displacement_atr_mult,
    )
    # Zone-strict (default ICT): effective_bias requires trend AND matching zone.
    # Trend-only (relaxed): use bias.trend directly, ignore zone.
    effective_bias = bias.bias if p.bias_zone_required else bias.trend
    if effective_bias == Trend.NEUTRAL:
        return None

    # ---- 2. Killzone ---------------------------------------------------- #
    bar_ts = df_ltf["ts"][ltf_bar_index]
    if p.require_killzone and not is_active_killzone(bar_ts):
        return None

    # ---- 3. LTF analysis: swings, ATR, pools --------------------------- #
    ltf_swings = find_swings(df_ltf, lookback=p.swing_lookback)
    ltf_atr = compute_atr(df_ltf)
    cur_atr = _safe_float(ltf_atr[ltf_bar_index] if ltf_bar_index < ltf_atr.len() else None)
    if cur_atr <= 0:
        return None

    pool_tolerance = p.equal_level_tolerance_atr * cur_atr
    all_pools = find_pools(
        ltf_swings,
        tolerance_price=pool_tolerance,
        min_count=p.pool_min_touches,
    )
    pools_now = active_pools_at(all_pools, ltf_bar_index, min_age_bars=p.pool_min_age_bars)

    # Bias yönüne göre HEDEF pool tipi:
    #   BULL bias → SSL sweep (price went down to grab sell stops, then bullish reversal)
    #   BEAR bias → BSL sweep (price popped up to grab buy stops, then bearish reversal)
    target_pool_type = PoolType.SSL if effective_bias == Trend.BULL else PoolType.BSL
    target_pools = [pool for pool in pools_now if pool.type == target_pool_type]
    if not target_pools:
        return None

    # Scan back for the most recent sweep within sweep_lookback_bars.
    # ICT setups unfold over multiple bars: sweep happens, then MSS, then FVG,
    # then the current bar may be the FVG-retest entry trigger. Looking only at
    # the current bar misses the whole pattern.
    sweep = None
    lookback_start = max(0, ltf_bar_index - p.sweep_lookback_bars)
    for k in range(ltf_bar_index, lookback_start - 1, -1):  # scan backwards
        s = detect_sweep(df_ltf, k, target_pools, p.sweep_min_wick_pct)
        if s is not None:
            sweep = s
            break
    if sweep is None:
        return None

    # ---- 4. MSS in bias direction within window ------------------------ #
    events = detect_events(df_ltf, ltf_swings, atr=ltf_atr,
                           displacement_atr_mult=p.displacement_atr_mult)
    aligned_events = [
        e for e in events
        if e.direction == effective_bias
        and e.type in (EventType.MSS, EventType.CHOCH, EventType.BOS)
        and sweep.bar_index <= e.index <= ltf_bar_index
        and (e.index - sweep.bar_index) <= p.sweep_to_mss_max_bars
    ]
    if not aligned_events:
        return None
    mss = aligned_events[-1]

    # ---- 5. FVG in displacement window --------------------------------- #
    fvgs = find_fvgs(df_ltf, atr=ltf_atr, min_atr_mult=p.fvg_min_atr_mult)
    candidate_fvgs = [
        f for f in fvgs
        if f.direction == effective_bias
        and (mss.index - 1) <= f.middle_index <= (mss.index + p.mss_to_fvg_max_bars)
        and f.confirmed_at_index <= ltf_bar_index
        and (ltf_bar_index - f.middle_index) <= p.fvg_max_age_bars
    ]
    # Discard mitigated FVGs (price already retested the gap, edge gone)
    candidate_fvgs = [f for f in candidate_fvgs
                      if not is_fvg_mitigated(df_ltf, f, until_index=ltf_bar_index - 1)]
    if not candidate_fvgs:
        return None
    fvg = candidate_fvgs[-1]

    # ---- 6. Entry / SL / TP -------------------------------------------- #
    entry = fvg.midpoint
    min_sl_dist = p.min_sl_distance_atr * cur_atr

    if effective_bias == Trend.BULL:
        sweep_extreme = float(df_ltf["low"][sweep.bar_index])
        sl = compute_sl(
            side=Trend.BULL, sweep_extreme=sweep_extreme,
            atr=cur_atr, atr_buffer_mult=p.sl_buffer_atr,
            entry=entry, min_sl_distance=min_sl_dist,
        )
        tp_pool_type = PoolType.BSL
    else:  # BEAR
        sweep_extreme = float(df_ltf["high"][sweep.bar_index])
        sl = compute_sl(
            side=Trend.BEAR, sweep_extreme=sweep_extreme,
            atr=cur_atr, atr_buffer_mult=p.sl_buffer_atr,
            entry=entry, min_sl_distance=min_sl_dist,
        )
        tp_pool_type = PoolType.SSL

    tp_pools = [pool for pool in pools_now if pool.type == tp_pool_type]
    tp = compute_tp(
        entry=entry, sl=sl, side=effective_bias,
        pool_prices=[pool.price for pool in tp_pools],
        min_rr=p.min_rr, max_rr=p.max_rr,
    )
    if tp is None:
        return None
    rr = compute_rr(entry, sl, tp)
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk <= 0 or reward <= 0:
        return None

    # Fee impact filter — round-trip fee on a position big enough to risk
    # `risk_per_trade_pct × equity` would eat too much of the reward.
    # Per-unit fee proportion is `fee_rate × entry`; on a "$100 risk" trade,
    # size = risk_amount/risk → fee = size × entry × fee_rate
    #                              = risk_amount × (entry × fee_rate / risk)
    # As a fraction of reward: (entry × fee_rate / risk) × (risk / reward) × risk_amount / reward
    # Simpler: per unit fee/reward = entry × fee_rate / reward
    fee_per_unit = entry * p.fee_rate_round_trip
    if reward <= 0 or (fee_per_unit / reward) > p.max_fee_to_reward_ratio:
        return None

    return TradeSignal(
        symbol=symbol,
        side=effective_bias,
        entry_price=entry,
        stop_loss=sl,
        take_profit=tp,
        rr=rr,
        setup_name=SETUP_NAME,
        bar_index=ltf_bar_index,
        ts=bar_ts,
        meta={
            "sweep_bar_index": sweep.bar_index,
            "sweep_pool_price": sweep.pool.price,
            "sweep_wick": sweep.wick_size,
            "mss_bar_index": mss.index,
            "mss_event_type": mss.type.value,
            "fvg_middle_index": fvg.middle_index,
            "fvg_top": fvg.top,
            "fvg_bottom": fvg.bottom,
            "htf_trend": bias.trend.value,
            "htf_zone": bias.zone.value,
            "htf_range_high": bias.range_high,
            "htf_range_low": bias.range_low,
            "atr_at_signal": cur_atr,
        },
    )


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    f = float(value)
    if f != f:
        return 0.0
    return f


def compute_sl(
    *,
    side: Trend,
    sweep_extreme: float,
    atr: float,
    atr_buffer_mult: float,
    entry: float,
    min_sl_distance: float = 0.0,
) -> float:
    """Compute stop-loss with ATR buffer past the sweep wick.

    Bullish (long): SL = sweep_low  - (atr_buffer_mult × atr)
                       capped to be at least `min_sl_distance` below `entry`.
    Bearish (short): SL = sweep_high + (atr_buffer_mult × atr)
                        capped to be at least `min_sl_distance` above `entry`.

    `min_sl_distance` is enforced as a floor so that very tight sweeps don't
    produce SLs that get noise-stopped immediately.
    """
    buffer_amount = atr_buffer_mult * atr
    if side == Trend.BULL:
        raw_sl = sweep_extreme - buffer_amount
        # SL must sit at least `min_sl_distance` below entry; pick the lower
        # of (raw, entry-min_dist) so the buffer floor only widens, never tightens.
        return min(raw_sl, entry - min_sl_distance)
    if side == Trend.BEAR:
        raw_sl = sweep_extreme + buffer_amount
        return max(raw_sl, entry + min_sl_distance)
    raise ValueError(f"compute_sl: unsupported side {side}")


def compute_tp(
    *,
    entry: float,
    sl: float,
    side: Trend,
    pool_prices: list[float],
    min_rr: float,
    max_rr: float,
) -> float | None:
    """Pick the nearest liquidity pool whose distance lies in [min_rr, max_rr].

    Returns the pool price, or None if no pool sits in the valid band. Use this
    instead of the legacy "take the nearest pool then reject on RR" pattern,
    which threw away signals whose nearest pool happened to be too tight even
    when a farther valid pool existed.

    Bullish (side=BULL): pools above entry; nearest = smallest price in band.
    Bearish (side=BEAR): pools below entry; nearest = largest price in band.
    The lower bound is inclusive so a pool exactly at min_rr is accepted.
    """
    risk = abs(entry - sl)
    if risk <= 0 or not pool_prices:
        return None
    min_dist = min_rr * risk
    max_dist = max_rr * risk

    if side == Trend.BULL:
        valid = [
            p for p in pool_prices
            if (p - entry) >= min_dist and (p - entry) <= max_dist
        ]
        return min(valid) if valid else None
    if side == Trend.BEAR:
        valid = [
            p for p in pool_prices
            if (entry - p) >= min_dist and (entry - p) <= max_dist
        ]
        return max(valid) if valid else None
    return None


def compute_rr(entry: float, stop_loss: float, take_profit: float) -> float:
    """Reward / risk ratio. Returns 0.0 for degenerate inputs (zero risk)."""
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return 0.0
    reward = abs(take_profit - entry)
    return reward / risk


def passes_min_rr(entry: float, stop_loss: float, take_profit: float, min_rr: float) -> bool:
    """True iff RR(entry, sl, tp) >= min_rr. Used both inside `evaluate()` and
    by tests; centralising the check keeps the rule single-sourced."""
    return compute_rr(entry, stop_loss, take_profit) >= min_rr
