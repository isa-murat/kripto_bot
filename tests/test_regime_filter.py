"""Tests for src/filters/regime_filter.py — thin wrapper around classifier."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import polars as pl

from src.filters.regime_filter import is_regime_tradeable

UTC = timezone.utc


def _make_bars(closes: list[float], *, freq_hours: int = 4) -> pl.DataFrame:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    n = len(closes)
    ts = [start + timedelta(hours=freq_hours * i) for i in range(n)]
    opens = [closes[0]] + closes[:-1]
    highs = [max(o, c) * 1.01 for o, c in zip(opens, closes)]
    lows = [min(o, c) * 0.99 for o, c in zip(opens, closes)]
    return pl.DataFrame({
        "ts": ts, "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": [100.0] * n,
    })


def test_bullish_regime_tradeable_when_not_excluded():
    df = _make_bars([100.0 + i for i in range(40)])
    ok, label = is_regime_tradeable(df, exclude_ranging=True, exclude_trending=False)
    assert ok is True
    assert label == "bullish"


def test_bearish_regime_tradeable_when_not_excluded():
    df = _make_bars([200.0 - i for i in range(40)])
    ok, label = is_regime_tradeable(df, exclude_ranging=True, exclude_trending=False)
    assert ok is True
    assert label == "bearish"


def test_ranging_regime_blocked_when_excluded():
    df = _make_bars([100.0 + (1 if i % 2 == 0 else -1) * 0.3 for i in range(40)])
    ok, label = is_regime_tradeable(df, exclude_ranging=True, exclude_trending=False)
    assert ok is False
    assert label == "ranging"


def test_ranging_regime_tradeable_when_not_excluded():
    df = _make_bars([100.0 + (1 if i % 2 == 0 else -1) * 0.3 for i in range(40)])
    ok, label = is_regime_tradeable(df, exclude_ranging=False, exclude_trending=False)
    assert ok is True
    assert label == "ranging"


def test_trending_blocked_when_exclude_trending_true():
    df = _make_bars([100.0 + i for i in range(40)])
    ok, label = is_regime_tradeable(df, exclude_ranging=False, exclude_trending=True)
    assert ok is False
    assert label == "bullish"


def test_unknown_regime_treated_as_tradeable():
    """Insufficient bars → classifier returns None → 'unknown' → tradeable."""
    df = _make_bars([100.0 + i for i in range(5)])     # << lookback
    ok, label = is_regime_tradeable(df, exclude_ranging=True, exclude_trending=False)
    assert ok is True
    assert label == "unknown"
