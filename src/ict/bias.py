"""ICT Higher-Timeframe Bias: trend (BOS direction) + premium/discount zone.

Decision matrix for final entry-bias:

    HTF trend    Price zone        → Bias (action)
    ---------    ---------------   -----------------------------------------
    BULL         DISCOUNT          BULL  (look for long entries now)
    BEAR         PREMIUM           BEAR  (look for short entries now)
    BULL         PREMIUM           NEUTRAL (wait for retracement to discount)
    BEAR         DISCOUNT          NEUTRAL (wait for retracement to premium)
    *            EQUILIBRIUM       NEUTRAL (no edge)
    NEUTRAL      *                 NEUTRAL (no trend)

The HTF (1h in our MVP) bias is consumed by the LTF (5m) entry strategy:
    `sweep_fvg` only scans for setups whose direction matches `bias.bias`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

import polars as pl

from src.ict.structure import (
    Swing,
    SwingType,
    Trend,
    compute_atr,
    current_trend,
    detect_events,
    find_swings,
)


class PriceZone(str, Enum):
    PREMIUM = "premium"          # upper portion of the range — short territory
    DISCOUNT = "discount"        # lower portion — long territory
    EQUILIBRIUM = "equilibrium"  # mid zone — no positional edge


@dataclass(frozen=True)
class BiasState:
    bias: Trend                 # final action bias: BULL / BEAR / NEUTRAL
    trend: Trend                # raw HTF trend from BOS sequence
    zone: PriceZone             # where current price sits in the range
    range_high: float
    range_low: float
    range_mid: float
    current_price: float
    bar_index: int
    ts: datetime


def compute_bias(
    df_htf: pl.DataFrame,
    bar_index: int | None = None,
    *,
    swing_lookback: int = 5,
    range_lookback_swings: int = 4,
    premium_threshold: float = 0.50,
    discount_threshold: float = 0.50,
    displacement_atr_mult: float = 1.5,
) -> BiasState:
    """Compute HTF bias as of `bar_index` (default: last bar of df_htf).

    Inputs are restricted to information available *at or before* `bar_index`
    (look-ahead safe).
    """
    if df_htf.height == 0:
        raise ValueError("df_htf is empty")
    if bar_index is None:
        bar_index = df_htf.height - 1
    if not 0 <= bar_index < df_htf.height:
        raise IndexError(f"bar_index {bar_index} out of range [0, {df_htf.height})")

    price = float(df_htf["close"][bar_index])
    ts = df_htf["ts"][bar_index]

    # Swings — only those that became observable on or before bar_index
    all_swings = find_swings(df_htf, lookback=swing_lookback)
    visible_swings: list[Swing] = [s for s in all_swings if s.confirmed_at_index <= bar_index]

    # Trend from structure events up to bar_index
    atr = compute_atr(df_htf)
    all_events = detect_events(df_htf, visible_swings, atr=atr,
                               displacement_atr_mult=displacement_atr_mult)
    events_until_now = [e for e in all_events if e.index <= bar_index]
    trend = current_trend(events_until_now)

    # Premium/discount range from the most recent N swings of each type
    highs = [s for s in visible_swings if s.type == SwingType.HIGH][-range_lookback_swings:]
    lows = [s for s in visible_swings if s.type == SwingType.LOW][-range_lookback_swings:]

    if not highs or not lows:
        return BiasState(
            bias=Trend.NEUTRAL, trend=trend, zone=PriceZone.EQUILIBRIUM,
            range_high=price, range_low=price, range_mid=price,
            current_price=price, bar_index=bar_index, ts=ts,
        )

    range_high = max(s.price for s in highs)
    range_low = min(s.price for s in lows)
    if range_high <= range_low:
        return BiasState(
            bias=Trend.NEUTRAL, trend=trend, zone=PriceZone.EQUILIBRIUM,
            range_high=range_high, range_low=range_low,
            range_mid=(range_high + range_low) / 2.0,
            current_price=price, bar_index=bar_index, ts=ts,
        )

    range_mid = (range_high + range_low) / 2.0
    zone = _classify_zone(price, range_low, range_high,
                          premium_threshold, discount_threshold)
    bias = _final_bias(trend, zone)

    return BiasState(
        bias=bias, trend=trend, zone=zone,
        range_high=range_high, range_low=range_low, range_mid=range_mid,
        current_price=price, bar_index=bar_index, ts=ts,
    )


def _classify_zone(
    price: float,
    range_low: float,
    range_high: float,
    premium_threshold: float,
    discount_threshold: float,
) -> PriceZone:
    """Returns where `price` sits inside `[range_low, range_high]`.

    pct = (price - range_low) / (range_high - range_low)  ∈ [0, 1] (clamped semantics)
        > premium_threshold → PREMIUM
        < discount_threshold → DISCOUNT
        otherwise            → EQUILIBRIUM

    Defaults (0.50 / 0.50) split the range cleanly: anything above mid = premium,
    below = discount, exact mid = equilibrium.
    """
    span = range_high - range_low
    if span <= 0:
        return PriceZone.EQUILIBRIUM
    pct = (price - range_low) / span
    if pct > premium_threshold:
        return PriceZone.PREMIUM
    if pct < discount_threshold:
        return PriceZone.DISCOUNT
    return PriceZone.EQUILIBRIUM


def _final_bias(trend: Trend, zone: PriceZone) -> Trend:
    if trend == Trend.BULL and zone == PriceZone.DISCOUNT:
        return Trend.BULL
    if trend == Trend.BEAR and zone == PriceZone.PREMIUM:
        return Trend.BEAR
    return Trend.NEUTRAL
