"""Unit tests for src.ict.structure — synthetic OHLCV fixtures."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import polars as pl
import pytest

from src.ict.structure import (
    EventType,
    StructureEvent,
    Swing,
    SwingType,
    Trend,
    compute_atr,
    current_trend,
    detect_events,
    find_swings,
)

UTC = timezone.utc


def _bars(rows: list[tuple[float, float, float, float]]) -> pl.DataFrame:
    """Build a typed OHLCV DataFrame from (open, high, low, close) tuples.

    5m timestamp grid starting 2026-01-01 00:00 UTC. Explicit float cast keeps
    Polars from inferring Int64 when the first rows happen to be all-int.
    """
    n = len(rows)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return pl.DataFrame({
        "ts": [start + timedelta(minutes=5 * i) for i in range(n)],
        "open":   [float(r[0]) for r in rows],
        "high":   [float(r[1]) for r in rows],
        "low":    [float(r[2]) for r in rows],
        "close":  [float(r[3]) for r in rows],
        "volume": [100.0] * n,
    }).with_columns(pl.col("ts").cast(pl.Datetime("ms", time_zone="UTC")))


# =================================== find_swings ============================ #


def test_no_swings_when_too_few_bars():
    df = _bars([(1, 2, 0, 1)] * 4)  # lookback=3 needs >= 7 bars
    assert find_swings(df, lookback=3) == []


def test_single_swing_high_centered():
    # Index:    0    1    2    3 (peak)  4    5    6
    # high:     1    2    3    5         3    2    1
    rows = [
        (1, 1, 0, 1),
        (1, 2, 1, 1),
        (1, 3, 1, 1),
        (1, 5, 1, 4),
        (1, 3, 1, 1),
        (1, 2, 1, 1),
        (1, 1, 0, 1),
    ]
    swings = find_swings(_bars(rows), lookback=3)
    assert len(swings) == 1
    s = swings[0]
    assert s.type == SwingType.HIGH
    assert s.index == 3
    assert s.price == 5.0
    assert s.confirmed_at_index == 6  # index + lookback


def test_single_swing_low_centered():
    # mirror of the swing-high test
    rows = [
        (5, 6, 5, 6),
        (5, 6, 4, 5),
        (5, 6, 3, 4),
        (5, 6, 1, 2),  # trough
        (5, 6, 3, 4),
        (5, 6, 4, 5),
        (5, 6, 5, 6),
    ]
    swings = find_swings(_bars(rows), lookback=3)
    assert len(swings) == 1
    assert swings[0].type == SwingType.LOW
    assert swings[0].index == 3
    assert swings[0].price == 1.0


def test_alternating_swings_chronological_order():
    # high at 3, low at 7, high at 11
    rows = (
        [(1, 1, 0, 1), (1, 2, 1, 1), (1, 3, 1, 1)]
        + [(1, 5, 1, 4)]                                 # high  @3
        + [(1, 3, 1, 1), (1, 2, 1, 1), (1, 1, 0, 1)]
        + [(0, 1, -3, -2)]                               # low   @7
        + [(0, 1, -1, 0), (0, 2, -1, 1), (0, 3, 0, 2)]
        + [(0, 5, 0, 4)]                                 # high  @11
        + [(0, 3, 0, 1), (0, 2, 0, 1), (0, 1, 0, 0)]
    )
    swings = find_swings(_bars(rows), lookback=3)
    assert [s.type for s in swings] == [SwingType.HIGH, SwingType.LOW, SwingType.HIGH]
    assert [s.index for s in swings] == [3, 7, 11]
    # Must already be sorted by index (chronological)
    assert swings == sorted(swings, key=lambda x: x.index)


def test_strict_inequality_no_equal_high_double_count():
    # Two equal "highs" — neither is a valid fractal swing under strict >
    rows = [
        (1, 1, 0, 1),
        (1, 2, 1, 1),
        (1, 3, 1, 1),
        (1, 5, 1, 4),
        (1, 5, 1, 4),
        (1, 3, 1, 1),
        (1, 2, 1, 1),
        (1, 1, 0, 1),
    ]
    swings = find_swings(_bars(rows), lookback=3)
    assert all(s.type != SwingType.HIGH for s in swings)


# =================================== compute_atr ============================ #


def test_atr_first_period_minus_one_is_null():
    rows = [(10, 12, 8, 11)] * 20
    atr = compute_atr(_bars(rows), period=14)
    assert atr.len() == 20
    # First 13 (period-1) should be null because there's no full window
    nulls = atr.is_null().to_list()
    assert nulls[:13] == [True] * 13
    assert nulls[13] is False


def test_atr_constant_range():
    # Constant TR = 4 every bar (ignoring first which uses no prev_close)
    rows = [(10, 14, 10, 12)] * 20
    atr = compute_atr(_bars(rows), period=5)
    # After warmup, ATR should equal the constant TR = 4
    val = atr[10]
    assert val is not None
    assert abs(val - 4.0) < 1e-9


# =================================== detect_events ========================== #


def test_no_events_without_swings():
    df = _bars([(1, 2, 0, 1)] * 5)
    assert detect_events(df, swings=[], atr=None) == []


def test_initial_break_emits_bos_from_neutral():
    """First detected break sets the trend direction; classified as BOS."""
    # Build: a swing high at index 3 (price 5), then later bar breaks above 5.
    rows = [
        (1, 1, 0, 1),       # 0
        (1, 2, 1, 1),       # 1
        (1, 3, 1, 1),       # 2
        (1, 5, 1, 4),       # 3 ← swing high (price 5)
        (1, 3, 1, 1),       # 4
        (1, 4, 1, 3),       # 5
        (1, 4, 1, 3),       # 6 ← swing confirmed at index 6 (3+3)
        (3, 7, 2, 6),       # 7 ← break: high=7 > 5
    ]
    df = _bars(rows)
    swings = find_swings(df, lookback=3)
    events = detect_events(df, swings)
    assert len(events) == 1
    e = events[0]
    assert e.type == EventType.BOS
    assert e.direction == Trend.BULL
    assert e.index == 7
    assert e.broken_swing.price == 5.0


def test_choch_after_opposite_trend():
    """BULL trend established by first BOS, then a bearish break = CHoCH.

    Breaking bar body must stay below `displacement_atr_mult × ATR` so the
    event is classified as CHoCH and not upgraded to MSS.
    """
    rows = [
        # swing high @3
        (1, 1, 0, 1), (1, 2, 1, 1), (1, 3, 1, 1), (1, 5, 1, 4),
        (1, 3, 1, 1), (1, 4, 1, 3), (1, 4, 1, 3),
        # bullish break @7 → BOS BULL
        (3, 7, 2, 6),
        # swing low @11 (price -3)
        (5, 6, 4, 5), (5, 6, 3, 4), (5, 6, 2, 3),
        (5, 6, -3, -2),
        (5, 6, 1, 2), (5, 6, 2, 3), (5, 6, 3, 4),
        # bearish break @15 → low=-5 < -3, küçük body (1) → CHoCH (MSS değil)
        (-3, -3, -5, -4),
    ]
    df = _bars(rows)
    swings = find_swings(df, lookback=3)
    events = detect_events(df, swings)
    assert len(events) >= 2
    # First event: BOS BULL at index 7
    assert events[0].type == EventType.BOS
    assert events[0].direction == Trend.BULL
    # Second event: CHoCH BEAR (küçük body sayesinde MSS'e yükselmedi)
    assert events[1].type == EventType.CHOCH
    assert events[1].direction == Trend.BEAR
    assert events[1].index == 15


def test_mss_when_choch_has_displacement():
    """A CHoCH whose breaking bar's body > 1.5×ATR is upgraded to MSS."""
    # Build same setup as CHoCH test, but make the breaking bar a huge body.
    rows = [
        (1, 1, 0, 1), (1, 2, 1, 1), (1, 3, 1, 1), (1, 5, 1, 4),
        (1, 3, 1, 1), (1, 4, 1, 3), (1, 4, 1, 3),
        (3, 7, 2, 6),
        (5, 6, 4, 5), (5, 6, 3, 4), (5, 6, 2, 3),
        (5, 6, -3, -2),
        (5, 6, 1, 2), (5, 6, 2, 3), (5, 6, 3, 4),
        # Breaking bar with large body: open=5, close=-15 → body=20 (≫ ATR)
        (5, 6, -16, -15),
    ]
    df = _bars(rows)
    swings = find_swings(df, lookback=3)
    events = detect_events(df, swings, displacement_atr_mult=1.5)
    bearish = [e for e in events if e.direction == Trend.BEAR]
    assert bearish, "expected at least one bearish break"
    assert bearish[0].type == EventType.MSS


def test_current_trend_helper():
    assert current_trend([]) == Trend.NEUTRAL
    swing = Swing(
        index=0, confirmed_at_index=0,
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        price=1.0, type=SwingType.HIGH,
    )
    event = StructureEvent(
        index=1, ts=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        type=EventType.BOS, direction=Trend.BULL,
        broken_swing=swing, body_size=1.0, atr=1.0,
    )
    assert current_trend([event]) == Trend.BULL
