"""Unit tests for src.ict.bias — HTF bias decision matrix."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import polars as pl
import pytest

from src.ict.bias import (
    BiasState,
    PriceZone,
    _classify_zone,
    _final_bias,
    compute_bias,
)
from src.ict.structure import Trend

UTC = timezone.utc


def _bars(rows: list[tuple[float, float, float, float]]) -> pl.DataFrame:
    n = len(rows)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return pl.DataFrame({
        "ts": [start + timedelta(minutes=60 * i) for i in range(n)],  # 1h grid
        "open":   [float(r[0]) for r in rows],
        "high":   [float(r[1]) for r in rows],
        "low":    [float(r[2]) for r in rows],
        "close":  [float(r[3]) for r in rows],
        "volume": [100.0] * n,
    }).with_columns(pl.col("ts").cast(pl.Datetime("ms", time_zone="UTC")))


# ============================ _classify_zone ================================ #


def test_zone_at_low_is_discount():
    assert _classify_zone(100, 100, 200, 0.50, 0.50) == PriceZone.DISCOUNT


def test_zone_at_high_is_premium():
    assert _classify_zone(200, 100, 200, 0.50, 0.50) == PriceZone.PREMIUM


def test_zone_at_mid_is_equilibrium_with_default_thresholds():
    # pct = 0.5 → not > 0.5 (premium), not < 0.5 (discount) → equilibrium
    assert _classify_zone(150, 100, 200, 0.50, 0.50) == PriceZone.EQUILIBRIUM


def test_zone_below_mid_is_discount():
    assert _classify_zone(120, 100, 200, 0.50, 0.50) == PriceZone.DISCOUNT


def test_zone_above_mid_is_premium():
    assert _classify_zone(180, 100, 200, 0.50, 0.50) == PriceZone.PREMIUM


def test_zone_zero_span_is_equilibrium():
    assert _classify_zone(100, 100, 100, 0.50, 0.50) == PriceZone.EQUILIBRIUM


def test_zone_custom_thresholds_create_equilibrium_band():
    """premium=0.62, discount=0.38 leaves 0.38..0.62 as EQUILIBRIUM band."""
    assert _classify_zone(150, 100, 200, 0.62, 0.38) == PriceZone.EQUILIBRIUM
    assert _classify_zone(165, 100, 200, 0.62, 0.38) == PriceZone.PREMIUM
    assert _classify_zone(135, 100, 200, 0.62, 0.38) == PriceZone.DISCOUNT


# ============================== _final_bias ================================= #


def test_final_bias_matrix():
    matrix = [
        # (trend, zone, expected bias)
        (Trend.BULL,    PriceZone.DISCOUNT,    Trend.BULL),
        (Trend.BEAR,    PriceZone.PREMIUM,     Trend.BEAR),
        (Trend.BULL,    PriceZone.PREMIUM,     Trend.NEUTRAL),
        (Trend.BEAR,    PriceZone.DISCOUNT,    Trend.NEUTRAL),
        (Trend.BULL,    PriceZone.EQUILIBRIUM, Trend.NEUTRAL),
        (Trend.BEAR,    PriceZone.EQUILIBRIUM, Trend.NEUTRAL),
        (Trend.NEUTRAL, PriceZone.DISCOUNT,    Trend.NEUTRAL),
        (Trend.NEUTRAL, PriceZone.PREMIUM,     Trend.NEUTRAL),
    ]
    for trend, zone, expected in matrix:
        assert _final_bias(trend, zone) == expected, f"{trend} + {zone} → {expected}"


# ============================== compute_bias =============================== #


def test_compute_bias_empty_df_raises():
    df = _bars([])
    with pytest.raises(ValueError):
        compute_bias(df)


def test_compute_bias_no_swings_returns_neutral():
    """Flat data → no swings → no range → NEUTRAL bias."""
    rows = [(100, 101, 99, 100)] * 5
    state = compute_bias(_bars(rows), swing_lookback=3)
    assert state.bias == Trend.NEUTRAL
    assert state.zone == PriceZone.EQUILIBRIUM


_BULL_BASELINE: list[tuple[float, float, float, float]] = [
    # Pad
    (100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100),
    # Swing low @3 (price 90)
    (95, 96, 90, 92),
    (95, 100, 91, 99), (95, 102, 92, 101), (95, 105, 93, 104),
    # Swing high @7 (price 110)
    (105, 110, 104, 108),
    (105, 108, 102, 104), (105, 107, 101, 103),
    # Bar 10 deliberately flat (high=106, low=105) so it does NOT form a swing
    # low at 100 — that secondary low would be broken by the final discount bar
    # and flip the trend before bias is evaluated.
    (105, 106, 105, 105),
    # Bullish break above 110 @11 → BOS BULL
    (102, 115, 101, 114),
    # Pad bars maintaining swing high above last
    (114, 116, 110, 113), (114, 117, 110, 113), (114, 116, 109, 112),
    # New swing high @15 confirmed at 18
    (113, 120, 109, 119),
    (119, 119, 115, 116), (119, 119, 114, 115), (119, 119, 113, 114),
]


def test_compute_bias_bull_trend_with_price_in_discount():
    """Build BULL trend, then drop price into discount zone → BULL bias."""
    rows = [
        *_BULL_BASELINE,
        # Final bar: low above the original swing low (90), close in discount.
        (110, 111, 92, 93),
    ]
    df = _bars(rows)
    state = compute_bias(df, swing_lookback=3, range_lookback_swings=4)
    assert state.trend == Trend.BULL
    assert state.zone == PriceZone.DISCOUNT
    assert state.bias == Trend.BULL


def test_compute_bias_bull_trend_premium_returns_neutral():
    """Same BULL setup but current price near the high → bias NEUTRAL (wait)."""
    rows = [
        *_BULL_BASELINE,
        # Current price near the high (premium)
        (118, 119, 117, 119),
    ]
    df = _bars(rows)
    state = compute_bias(df, swing_lookback=3, range_lookback_swings=4)
    assert state.trend == Trend.BULL
    assert state.zone == PriceZone.PREMIUM
    assert state.bias == Trend.NEUTRAL


def test_compute_bias_respects_bar_index_no_lookahead():
    """Computing bias at an early bar must not use later swings."""
    # Same data as bull-trend test
    rows = [
        (100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100),
        (95, 96, 90, 92),
        (95, 100, 91, 99), (95, 102, 92, 101), (95, 105, 93, 104),
        (105, 110, 104, 108),
        (105, 108, 102, 104), (105, 107, 101, 103), (105, 106, 100, 102),
        (102, 115, 101, 114),
    ]
    df = _bars(rows)
    # At bar 4 we shouldn't have seen the swing high at index 7 or the BOS at 11
    state_early = compute_bias(df, bar_index=4, swing_lookback=3,
                               range_lookback_swings=4)
    # Insufficient swings of one type → NEUTRAL
    assert state_early.bias == Trend.NEUTRAL
    assert state_early.bar_index == 4


def test_compute_bias_returns_metadata():
    rows = [(100, 101, 99, 100)] * 10
    state = compute_bias(_bars(rows), swing_lookback=3)
    assert isinstance(state, BiasState)
    assert state.bar_index == 9
    assert state.current_price == 100.0
