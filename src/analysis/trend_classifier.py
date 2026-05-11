"""HTF trend regime classifier.

Two independent methods are run on the last `lookback` bars of an HTF (e.g.
4h-aggregated) DataFrame, and the result is the consensus:

- Swing structure: HH+HL → bullish, LH+LL → bearish, mixed → ranging
- EMA slope: (ema20[-1] - ema20[-lookback]) / ATR vs threshold

If both methods agree, return that label. If they disagree, return 'ranging'
(deliberately conservative — a regime label that gets used for filtering
should only fire when the signal is unambiguous). Returns None when the
DataFrame doesn't have enough bars for either method to run.

Public API:
    classify_htf_trend(df, lookback=20) -> 'bullish' | 'bearish' | 'ranging' | None
    aggregate_to_4h(df_1h) -> pl.DataFrame
"""

from __future__ import annotations

from typing import Literal, Optional

import polars as pl

TrendLabel = Literal["bullish", "bearish", "ranging"]

# How much the EMA must move (in ATR units) over `lookback` bars to count as
# a trending regime. Chosen empirically: 0.5 ATR over 20 bars is ~modest slope.
EMA_SLOPE_ATR_THRESHOLD = 0.5

# Minimum bars needed for either method. EMA needs `lookback` + a few for the
# EMA itself to stabilise; swing method needs at least 2 highs and 2 lows.
MIN_BARS_FOR_SWING = 8


def aggregate_to_4h(df_1h: pl.DataFrame) -> pl.DataFrame:
    """Aggregate 1h OHLCV bars into 4h bars.

    Drops any partial 4h bar at the tail so look-ahead bias can't leak in.
    """
    if df_1h.height == 0:
        return df_1h
    agg = (
        df_1h.sort("ts")
        .group_by_dynamic("ts", every="4h", closed="left", label="left")
        .agg([
            pl.col("open").first(),
            pl.col("high").max(),
            pl.col("low").min(),
            pl.col("close").last(),
            pl.col("volume").sum() if "volume" in df_1h.columns else pl.lit(0).alias("volume"),
        ])
    )
    # Drop the last group if it represents an incomplete 4h window: heuristic
    # check — if fewer than 4 source rows fell into it, treat it as partial.
    # (Polars' group_by_dynamic does include partial trailing buckets.)
    counts = (
        df_1h.sort("ts")
        .group_by_dynamic("ts", every="4h", closed="left", label="left")
        .agg(pl.len().alias("n"))
    )
    if counts.height > 0 and int(counts["n"][-1]) < 4:
        agg = agg.slice(0, agg.height - 1)
    return agg


def _swing_method(df: pl.DataFrame, lookback: int) -> Optional[TrendLabel]:
    """Trend label from HH/HL vs LH/LL pattern over last `lookback` bars."""
    if df.height < max(MIN_BARS_FOR_SWING, lookback):
        return None
    window = df.tail(lookback)
    highs = window["high"].to_list()
    lows = window["low"].to_list()

    # Find local maxima/minima with a tiny 1-bar lookback fractal (since the
    # window is small, full ICT fractal would yield <2 pivots). Compare the
    # first half's highest-high vs second half's highest-high, same for lows.
    mid = lookback // 2
    first_half_hh = max(highs[:mid])
    second_half_hh = max(highs[mid:])
    first_half_ll = min(lows[:mid])
    second_half_ll = min(lows[mid:])

    higher_highs = second_half_hh > first_half_hh
    higher_lows = second_half_ll > first_half_ll
    lower_highs = second_half_hh < first_half_hh
    lower_lows = second_half_ll < first_half_ll

    if higher_highs and higher_lows:
        return "bullish"
    if lower_highs and lower_lows:
        return "bearish"
    return "ranging"


def _atr(df: pl.DataFrame, period: int = 14) -> float:
    """Simple ATR over the last `period` bars."""
    if df.height < 2:
        return 0.0
    n = min(df.height, period + 1)
    sub = df.tail(n)
    highs = sub["high"].to_list()
    lows = sub["low"].to_list()
    closes = sub["close"].to_list()
    trs: list[float] = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0.0


def _ema_method(df: pl.DataFrame, lookback: int) -> Optional[TrendLabel]:
    """Trend label from EMA(20) slope normalised by ATR(14)."""
    if df.height < lookback + 20:
        return None
    closes = df["close"].to_list()
    # EMA(20) over the whole series
    alpha = 2.0 / (20 + 1)
    ema_curr = closes[0]
    ema_series: list[float] = [ema_curr]
    for c in closes[1:]:
        ema_curr = alpha * c + (1 - alpha) * ema_curr
        ema_series.append(ema_curr)

    ema_now = ema_series[-1]
    ema_then = ema_series[-lookback]
    atr = _atr(df, period=14)
    if atr <= 0:
        return None
    slope_atr = (ema_now - ema_then) / atr

    if slope_atr > EMA_SLOPE_ATR_THRESHOLD:
        return "bullish"
    if slope_atr < -EMA_SLOPE_ATR_THRESHOLD:
        return "bearish"
    return "ranging"


def classify_htf_trend(
    htf_bars: pl.DataFrame,
    lookback: int = 20,
) -> Optional[TrendLabel]:
    """Return 'bullish' | 'bearish' | 'ranging' from the last `lookback` HTF bars.

    Requires columns `open, high, low, close` (and ideally `ts`). Returns None
    if there aren't enough bars to run either method, or if the two methods
    disagree with one returning None.
    """
    if htf_bars.height < lookback:
        return None

    swing = _swing_method(htf_bars, lookback)
    ema = _ema_method(htf_bars, lookback)

    if swing is None and ema is None:
        return None
    # If only one method yields a verdict, trust it.
    if swing is None:
        return ema
    if ema is None:
        return swing
    # Both agree → that label. Disagreement → ranging (conservative).
    if swing == ema:
        return swing
    return "ranging"
