"""OTE (Optimal Trade Entry) — klasik ICT, trend-following entry on fib retracement.

Pipeline (per LTF bar close):
    1. HTF bias       → BULL or BEAR (NEUTRAL → no signal)
    2. Killzone       → optional (default off; F-06: Forex killzones don't transfer)
    3. 5m MSS         → bias-aligned MSS within `mss_lookback_bars`
    4. Impulse leg    → leg base (swing extreme before MSS) → leg apex (extreme since MSS)
    5. Fib zone       → current bar's low/high taps [0.786 .. 0.618] retrace zone
    6. First-touch    → previous bars since MSS did NOT touch the zone yet
    7. Entry/SL/TP    → entry at 0.705 sweet spot, SL past leg base + ATR buffer,
                        TP at fixed `tp_r_multiple` × risk (default 2R per F-03 baseline)

The function is pure: takes data + parameters, returns a `TradeSignal | None`.
Re-uses `TradeSignal` from sweep_fvg so the rest of the engine (SignalRouter,
PaperBroker) doesn't care which strategy fired.

Lessons applied from findings.md:
    F-02 — N≥200 validation enforced by backtest runner, not strategy
    F-04 — fee filter uses entry × fee_rate / reward
    F-05 — regime filter is upstream (SignalRouter), not in this module
    F-06 — `require_killzone` defaults False
    F-11 — IS+OOS two-window validation is backtest-level
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import polars as pl

from src.config import StrategyParams
from src.ict.bias import compute_bias
from src.ict.killzone import is_active_killzone
from src.ict.structure import (
    EventType,
    Trend,
    compute_atr,
    detect_events,
    find_swings,
)
from src.strategies.sweep_fvg import TradeSignal


@dataclass
class SetupParams:
    # Structure / MSS detection
    swing_lookback: int = 3
    displacement_atr_mult: float = 1.5
    # How far back to look for a fresh bias-aligned MSS
    mss_lookback_bars: int = 30
    # Window before MSS to search for the impulse leg base (swing extreme).
    # Wider window finds older legs (deeper retraces); narrower keeps legs tight.
    max_leg_lookback_bars: int = 50
    # Minimum leg amplitude in ATR. Tiny legs produce zones that overlap noise.
    min_leg_atr_mult: float = 1.5
    # Fib zone bounds. 0.618-0.786 is canonical ICT OTE. 0.705 is the sweet spot
    # used for the entry limit price.
    fib_zone_low: float = 0.618
    fib_zone_high: float = 0.786
    fib_entry_target: float = 0.705
    # SL = leg_base ± sl_buffer_atr × ATR (past the leg base)
    sl_buffer_atr: float = 0.30
    # En sıkışık SL bile entry'den bu kadar uzakta olsun
    min_sl_distance_atr: float = 0.50
    # Fixed R-multiple TP. F-03 says volatility-aware later; baseline is 2R.
    tp_r_multiple: float = 2.0
    # Round-trip fee budget vs reward (same idea as sweep_fvg)
    max_fee_to_reward_ratio: float = 0.30
    fee_rate_round_trip: float = 0.0008
    # HTF (bias) tuning
    htf_swing_lookback: int = 5
    htf_range_lookback_swings: int = 4
    htf_premium_threshold: float = 0.50
    htf_discount_threshold: float = 0.50
    # F-06: default off. Crypto doesn't honour Forex session killzones empirically.
    require_killzone: bool = False
    # If True, only the HTF trend is used for bias gating (zone ignored).
    # Same semantics as sweep_fvg.
    bias_zone_required: bool = False

    @classmethod
    def from_strategy_params(cls, sp: StrategyParams) -> "SetupParams":
        s = sp.structure or {}
        b = sp.bias or {}
        o = getattr(sp, "setup_ote", None) or {}
        return cls(
            swing_lookback=int(s.get("swing_lookback_5m", 3)),
            displacement_atr_mult=float(s.get("displacement_atr_mult", 1.5)),
            mss_lookback_bars=int(o.get("mss_lookback_bars", 30)),
            max_leg_lookback_bars=int(o.get("max_leg_lookback_bars", 50)),
            min_leg_atr_mult=float(o.get("min_leg_atr_mult", 1.5)),
            fib_zone_low=float(o.get("fib_zone_low", 0.618)),
            fib_zone_high=float(o.get("fib_zone_high", 0.786)),
            fib_entry_target=float(o.get("fib_entry_target", 0.705)),
            sl_buffer_atr=float(o.get("sl_buffer_atr", 0.30)),
            min_sl_distance_atr=float(o.get("min_sl_distance_atr", 0.50)),
            tp_r_multiple=float(o.get("tp_r_multiple", 2.0)),
            max_fee_to_reward_ratio=float(o.get("max_fee_to_reward_ratio", 0.30)),
            fee_rate_round_trip=float(o.get("fee_rate_round_trip", 0.0008)),
            require_killzone=bool(o.get("require_killzone", False)),
            bias_zone_required=bool(o.get("bias_zone_required", False)),
            htf_swing_lookback=int(s.get("swing_lookback_1h", 5)),
            htf_range_lookback_swings=int(b.get("range_lookback_swings", 4)),
            htf_premium_threshold=float(b.get("premium_threshold", 0.50)),
            htf_discount_threshold=float(b.get("discount_threshold", 0.50)),
        )


SETUP_NAME = "ote"


def evaluate(
    *,
    symbol: str,
    df_ltf: pl.DataFrame,
    df_htf: pl.DataFrame,
    ltf_bar_index: int | None = None,
    htf_bar_index: int | None = None,
    params: SetupParams | None = None,
) -> TradeSignal | None:
    """Evaluate the OTE setup at `ltf_bar_index`. Returns a signal or None.

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
    effective_bias = bias.bias if p.bias_zone_required else bias.trend
    if effective_bias == Trend.NEUTRAL:
        return None

    # ---- 2. Killzone (default off) ------------------------------------- #
    bar_ts = df_ltf["ts"][ltf_bar_index]
    if p.require_killzone and not is_active_killzone(bar_ts):
        return None

    # ---- 3. 5m structure: latest bias-aligned MSS ---------------------- #
    ltf_swings = find_swings(df_ltf, lookback=p.swing_lookback)
    ltf_atr = compute_atr(df_ltf)
    cur_atr = _safe_float(ltf_atr[ltf_bar_index] if ltf_bar_index < ltf_atr.len() else None)
    if cur_atr <= 0:
        return None

    events = detect_events(
        df_ltf, ltf_swings, atr=ltf_atr,
        displacement_atr_mult=p.displacement_atr_mult,
    )
    mss_lookback_start = max(0, ltf_bar_index - p.mss_lookback_bars)
    aligned_mss = [
        e for e in events
        if e.direction == effective_bias
        and e.type in (EventType.MSS, EventType.CHOCH, EventType.BOS)
        and mss_lookback_start <= e.index <= ltf_bar_index
    ]
    if not aligned_mss:
        return None
    mss = aligned_mss[-1]

    # ---- 4. Impulse leg ------------------------------------------------ #
    leg_base_search_start = max(0, mss.index - p.max_leg_lookback_bars)
    leg_apex_end = ltf_bar_index  # leg can extend up to current bar

    if effective_bias == Trend.BULL:
        # leg base = lowest low in [leg_base_search_start, mss.index]
        leg_base_slice = df_ltf["low"][leg_base_search_start: mss.index + 1].to_numpy()
        if leg_base_slice.size == 0:
            return None
        leg_base_price = float(leg_base_slice.min())
        # leg apex = highest high in [mss.index, ltf_bar_index]
        leg_apex_slice = df_ltf["high"][mss.index: leg_apex_end + 1].to_numpy()
        if leg_apex_slice.size == 0:
            return None
        leg_apex_price = float(leg_apex_slice.max())
        leg_amplitude = leg_apex_price - leg_base_price
    else:  # BEAR
        leg_base_slice = df_ltf["high"][leg_base_search_start: mss.index + 1].to_numpy()
        if leg_base_slice.size == 0:
            return None
        leg_base_price = float(leg_base_slice.max())
        leg_apex_slice = df_ltf["low"][mss.index: leg_apex_end + 1].to_numpy()
        if leg_apex_slice.size == 0:
            return None
        leg_apex_price = float(leg_apex_slice.min())
        leg_amplitude = leg_base_price - leg_apex_price

    # Reject tiny legs — fib zone width would be in noise range.
    if leg_amplitude < p.min_leg_atr_mult * cur_atr:
        return None

    # ---- 5. Fib zone --------------------------------------------------- #
    # For BULL retrace: zone_low (deeper retrace, near leg base) at fib_zone_high,
    # zone_high (shallower retrace) at fib_zone_low. The "zone" between them is
    # the OTE band.
    if effective_bias == Trend.BULL:
        zone_low = leg_apex_price - p.fib_zone_high * leg_amplitude
        zone_high = leg_apex_price - p.fib_zone_low * leg_amplitude
        entry = leg_apex_price - p.fib_entry_target * leg_amplitude
    else:  # BEAR
        zone_low = leg_apex_price + p.fib_zone_low * leg_amplitude
        zone_high = leg_apex_price + p.fib_zone_high * leg_amplitude
        entry = leg_apex_price + p.fib_entry_target * leg_amplitude

    # ---- 6. First-touch on current bar --------------------------------- #
    # The current bar must tap the zone, AND prior bars since MSS must NOT have
    # touched it yet (otherwise we missed the entry on an earlier bar).
    cur_low = float(df_ltf["low"][ltf_bar_index])
    cur_high = float(df_ltf["high"][ltf_bar_index])
    if effective_bias == Trend.BULL:
        cur_touched = cur_low <= zone_high and cur_low >= zone_low
        if not cur_touched:
            return None
        # Prior bars (mss.index+1 .. ltf_bar_index-1): low must stay above zone_high
        if mss.index + 1 <= ltf_bar_index - 1:
            prior_lows = df_ltf["low"][mss.index + 1: ltf_bar_index].to_numpy()
            if prior_lows.size and float(prior_lows.min()) <= zone_high:
                return None
    else:  # BEAR
        cur_touched = cur_high >= zone_low and cur_high <= zone_high
        if not cur_touched:
            return None
        if mss.index + 1 <= ltf_bar_index - 1:
            prior_highs = df_ltf["high"][mss.index + 1: ltf_bar_index].to_numpy()
            if prior_highs.size and float(prior_highs.max()) >= zone_low:
                return None

    # ---- 7. Entry / SL / TP ------------------------------------------- #
    min_sl_dist = p.min_sl_distance_atr * cur_atr
    if effective_bias == Trend.BULL:
        sl = min(leg_base_price - p.sl_buffer_atr * cur_atr, entry - min_sl_dist)
        risk = entry - sl
        if risk <= 0:
            return None
        tp = entry + p.tp_r_multiple * risk
    else:
        sl = max(leg_base_price + p.sl_buffer_atr * cur_atr, entry + min_sl_dist)
        risk = sl - entry
        if risk <= 0:
            return None
        tp = entry - p.tp_r_multiple * risk

    reward = abs(tp - entry)
    if reward <= 0:
        return None

    # Fee filter: per-unit round-trip fee must not eat too much of the reward.
    fee_per_unit = entry * p.fee_rate_round_trip
    if (fee_per_unit / reward) > p.max_fee_to_reward_ratio:
        return None

    rr = reward / risk

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
            "mss_bar_index": mss.index,
            "mss_event_type": mss.type.value,
            "leg_base_price": leg_base_price,
            "leg_apex_price": leg_apex_price,
            "leg_amplitude": leg_amplitude,
            "fib_zone_low": zone_low,
            "fib_zone_high": zone_high,
            "fib_entry_target": p.fib_entry_target,
            "htf_trend": bias.trend.value,
            "htf_zone": bias.zone.value,
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
