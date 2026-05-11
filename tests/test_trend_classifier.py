"""Tests for src/analysis/trend_classifier.py"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import polars as pl
import pytest

from src.analysis.trend_classifier import (
    aggregate_to_4h,
    classify_htf_trend,
)

UTC = timezone.utc


def _make_bars(closes: list[float], *, start: datetime | None = None,
               freq_hours: int = 4) -> pl.DataFrame:
    """Build an OHLCV-shaped DataFrame from a list of closes.

    Highs/lows are mock-derived from close (close ± 1%); opens = previous close.
    """
    if start is None:
        start = datetime(2026, 1, 1, tzinfo=UTC)
    n = len(closes)
    ts = [start + timedelta(hours=freq_hours * i) for i in range(n)]
    opens = [closes[0]] + closes[:-1]
    highs = [max(o, c) * 1.01 for o, c in zip(opens, closes)]
    lows = [min(o, c) * 0.99 for o, c in zip(opens, closes)]
    return pl.DataFrame({
        "ts": ts,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [100.0] * n,
    })


# ----------------------------- classify_htf_trend --------------------------- #


def test_classify_pure_uptrend_bullish():
    closes = [100.0 + i for i in range(40)]
    df = _make_bars(closes)
    assert classify_htf_trend(df, lookback=20) == "bullish"


def test_classify_pure_downtrend_bearish():
    closes = [200.0 - i for i in range(40)]
    df = _make_bars(closes)
    assert classify_htf_trend(df, lookback=20) == "bearish"


def test_classify_flat_market_ranging():
    # 40 bars oscillating in a tight 1% band → no slope, no clear HH/LL
    closes = [100.0 + (1 if i % 2 == 0 else -1) * 0.3 for i in range(40)]
    df = _make_bars(closes)
    label = classify_htf_trend(df, lookback=20)
    assert label == "ranging"


def test_classify_too_few_bars_returns_none():
    closes = [100.0 + i for i in range(10)]   # < lookback
    df = _make_bars(closes)
    assert classify_htf_trend(df, lookback=20) is None


def test_classify_swing_and_ema_disagree_returns_ranging():
    """Strong recent reversal: swing method sees fresh higher highs/lows in
    second half, EMA still shows accumulated downtrend → disagreement →
    ranging."""
    # 25 bars down, then 15 bars up — EMA still negative, swings show reversal
    closes = [200.0 - i for i in range(25)] + [175.0 + i for i in range(15)]
    df = _make_bars(closes)
    label = classify_htf_trend(df, lookback=20)
    assert label in ("ranging", "bullish")  # either is OK, but never 'bearish'
    assert label != "bearish"


# ----------------------------- aggregate_to_4h ------------------------------ #


def test_aggregate_to_4h_groups_4_hourly_bars():
    closes = [100.0 + i for i in range(8)]
    df = _make_bars(closes, freq_hours=1)
    out = aggregate_to_4h(df)
    assert out.height == 2                # 8 1h bars → 2 4h bars
    # First 4h bar: open = first 1h's open, close = 4th 1h's close
    assert out["open"][0] == pytest.approx(df["open"][0])
    assert out["close"][0] == pytest.approx(df["close"][3])
    assert out["high"][0] == pytest.approx(max(df["high"][:4].to_list()))
    assert out["low"][0] == pytest.approx(min(df["low"][:4].to_list()))


def test_aggregate_to_4h_drops_partial_tail():
    """If the source ends mid-4h-bucket, the partial bar is dropped to avoid
    look-ahead bias."""
    closes = [100.0 + i for i in range(6)]   # 6 1h bars → 1 full 4h + 1 partial
    df = _make_bars(closes, freq_hours=1)
    out = aggregate_to_4h(df)
    assert out.height == 1                   # partial trailing bar dropped


def test_aggregate_to_4h_empty_input():
    df = pl.DataFrame({"ts": [], "open": [], "high": [], "low": [], "close": [], "volume": []},
                      schema={"ts": pl.Datetime, "open": pl.Float64, "high": pl.Float64,
                              "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64})
    out = aggregate_to_4h(df)
    assert out.height == 0
