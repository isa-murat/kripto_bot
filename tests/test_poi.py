"""Unit tests for src.ict.poi — FVG and Order Block detection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import polars as pl

from src.ict.poi import (
    FVG,
    OrderBlock,
    active_fvgs_at,
    find_fvgs,
    find_order_blocks,
    is_fvg_mitigated,
)
from src.ict.structure import Trend

UTC = timezone.utc


def _bars(rows: list[tuple[float, float, float, float]]) -> pl.DataFrame:
    n = len(rows)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    # Explicit float cast — int-only first rows would otherwise make Polars
    # infer Int64 and reject later float rows.
    return pl.DataFrame({
        "ts": [start + timedelta(minutes=5 * i) for i in range(n)],
        "open":   [float(r[0]) for r in rows],
        "high":   [float(r[1]) for r in rows],
        "low":    [float(r[2]) for r in rows],
        "close":  [float(r[3]) for r in rows],
        "volume": [100.0] * n,
    }).with_columns(pl.col("ts").cast(pl.Datetime("ms", time_zone="UTC")))


# ============================== FVG ========================================= #


def test_no_fvgs_when_too_few_bars():
    df = _bars([(100, 101, 99, 100), (100, 101, 99, 100)])
    assert find_fvgs(df) == []


def test_bullish_fvg_basic():
    """Classic 3-bar bullish gap: bar0.high < bar2.low."""
    rows = [
        (100, 101, 99, 100.5),     # bar0 high=101
        (102, 110, 101.5, 109),    # bar1 (displacement)
        (109, 112, 105, 111),      # bar2 low=105 → gap (101, 105)
    ]
    fvgs = find_fvgs(_bars(rows), min_atr_mult=0.0)
    assert len(fvgs) == 1
    f = fvgs[0]
    assert f.direction == Trend.BULL
    assert f.middle_index == 1
    assert f.bottom == 101.0       # bar[i-1].high
    assert f.top == 105.0          # bar[i+1].low
    assert f.size == 4.0
    assert f.midpoint == 103.0
    assert f.confirmed_at_index == 2


def test_bearish_fvg_basic():
    """Mirror of bullish — gap downward."""
    rows = [
        (109, 110, 105, 105.5),    # bar0 low=105
        (104, 105, 95, 96),        # bar1 (displacement down)
        (95, 100, 90, 92),         # bar2 high=100 → gap (100, 105)
    ]
    fvgs = find_fvgs(_bars(rows), min_atr_mult=0.0)
    assert len(fvgs) == 1
    f = fvgs[0]
    assert f.direction == Trend.BEAR
    assert f.bottom == 100.0       # bar[i+1].high
    assert f.top == 105.0          # bar[i-1].low


def test_no_fvg_when_bars_overlap():
    """If bar0 and bar2 ranges overlap, there is no gap."""
    rows = [
        (100, 105, 99, 102),
        (101, 106, 100, 104),
        (103, 107, 100, 105),  # bar2.low=100 not > bar0.high=105 → no bull FVG
    ]
    assert find_fvgs(_bars(rows), min_atr_mult=0.0) == []


def test_fvg_min_size_filter_rejects_tiny_gaps():
    """A 0.5-unit gap with ATR ~ 5 and min_atr_mult=0.5 → 2.5 threshold → reject."""
    # Build a longer series so ATR has a non-trivial value
    base = [(100, 105, 95, 100)] * 16  # ATR(14) ~ 10
    base.append((100, 101, 99, 100))            # bar 16 high=101
    base.append((101, 102, 101, 101))           # bar 17 (small displacement)
    base.append((101.5, 103, 101.4, 102))       # bar 18 low=101.4 → gap=0.4
    df = _bars(base)
    fvgs = find_fvgs(df, min_atr_mult=0.5)
    # Must not include any tiny gap at index 17
    assert all(f.middle_index != 17 for f in fvgs)


def test_fvg_min_atr_mult_zero_keeps_everything():
    rows = [
        (100, 101, 99, 100.5),
        (102, 110, 101.5, 109),
        (109, 112, 105, 111),
    ]
    assert len(find_fvgs(_bars(rows), min_atr_mult=0.0)) == 1


# ============================ active_fvgs_at ================================ #


def test_active_fvgs_filters_unconfirmed_and_aged():
    f_fresh = FVG(
        direction=Trend.BULL, middle_index=10,
        middle_ts=datetime(2026, 1, 1, tzinfo=UTC),
        top=105, bottom=100, confirmed_at_index=11,
    )
    f_old = FVG(
        direction=Trend.BULL, middle_index=2,
        middle_ts=datetime(2026, 1, 1, tzinfo=UTC),
        top=105, bottom=100, confirmed_at_index=3,
    )
    f_future = FVG(
        direction=Trend.BULL, middle_index=20,
        middle_ts=datetime(2026, 1, 1, tzinfo=UTC),
        top=105, bottom=100, confirmed_at_index=21,
    )
    active = active_fvgs_at([f_fresh, f_old, f_future], bar_index=15, max_age_bars=10)
    # f_fresh: 15-10=5 ≤ 10 ✓; f_old: 15-2=13 > 10 ✗; f_future: confirmed_at=21 > 15 ✗
    assert len(active) == 1
    assert active[0].middle_index == 10


# ============================ is_fvg_mitigated ============================== #


def test_fvg_not_mitigated_when_price_stays_outside():
    rows = [
        (100, 101, 99, 100.5),
        (102, 110, 101.5, 109),
        (109, 112, 105, 111),         # FVG (101, 105)
        (111, 113, 106, 112),         # bar3: low=106 > top=105 → outside
        (112, 114, 107, 113),         # bar4: low=107 → outside
    ]
    df = _bars(rows)
    fvgs = find_fvgs(df, min_atr_mult=0.0)
    assert len(fvgs) == 1
    assert is_fvg_mitigated(df, fvgs[0], until_index=4) is False


def test_fvg_mitigated_when_price_returns_into_zone():
    rows = [
        (100, 101, 99, 100.5),
        (102, 110, 101.5, 109),
        (109, 112, 105, 111),         # FVG (101, 105)
        (111, 113, 106, 112),
        (112, 113, 102, 103),         # bar4: low=102 inside (101..105) ✓
    ]
    df = _bars(rows)
    fvgs = find_fvgs(df, min_atr_mult=0.0)
    assert is_fvg_mitigated(df, fvgs[0], until_index=4) is True


# ============================ find_order_blocks ============================= #


def test_no_order_blocks_when_no_displacement():
    """Tiny bodies, ATR-relative — no displacement, no OB."""
    rows = [(100, 101, 99, 100.5)] * 20
    assert find_order_blocks(_bars(rows)) == []


def test_bullish_order_block():
    """Bear candle followed by big bull displacement → bull OB."""
    # Build steady history so ATR ~ 1
    rows = [(100, 101, 99, 100)] * 16
    rows.append((100, 101, 99, 99.5))       # bar 16: bear candle (close<open) → potential OB
    rows.append((99.5, 110, 99, 109))       # bar 17: huge bull displacement (body=9.5 ≫ ATR)
    df = _bars(rows)
    obs = find_order_blocks(df, displacement_atr_mult=1.5, lookback=5)
    bull = [o for o in obs if o.direction == Trend.BULL]
    assert bull, "expected at least one bull OB"
    assert bull[0].index == 16
    assert bull[0].close < bull[0].open    # OB is the bear candle


def test_bearish_order_block():
    """Bull candle followed by big bear displacement → bear OB."""
    rows = [(100, 101, 99, 100)] * 16
    rows.append((100, 101, 99, 100.8))      # bar 16: bull candle → potential bear OB
    rows.append((100.8, 101, 90, 91))       # bar 17: huge bear displacement
    df = _bars(rows)
    obs = find_order_blocks(df, displacement_atr_mult=1.5, lookback=5)
    bear = [o for o in obs if o.direction == Trend.BEAR]
    assert bear
    assert bear[0].index == 16
    assert bear[0].close > bear[0].open


def test_order_block_lookback_limit():
    """If the only opposite candle is beyond `lookback`, no OB is emitted."""
    rows = [(100, 101, 99, 100)] * 10
    rows[5] = (100, 101, 99, 99.5)          # bear at index 5
    # 6..15 all neutral/bull
    rows.extend([(100, 101, 99, 100.5)] * 10)
    rows.append((100, 110, 99, 109))        # bar 21: displacement
    df = _bars(rows)
    obs = find_order_blocks(df, displacement_atr_mult=1.5, lookback=5)
    # The bear at index 5 is 16 bars away, beyond lookback=5
    assert all(o.index != 5 for o in obs)


def test_order_block_unique_per_index_and_direction():
    """Same (index, direction) should not be emitted twice."""
    rows = [(100, 101, 99, 100)] * 16
    rows.append((100, 101, 99, 99.5))       # bar 16: bear candle → bull OB
    rows.append((99.5, 110, 99, 109))       # bar 17: bull disp #1
    rows.append((109, 110, 108, 109))       # bar 18: small bar
    rows.append((109, 120, 108, 119))       # bar 19: bull disp #2 — should NOT re-emit OB at 16
    df = _bars(rows)
    obs = find_order_blocks(df, displacement_atr_mult=1.5, lookback=10)
    bull_at_16 = [o for o in obs if o.direction == Trend.BULL and o.index == 16]
    assert len(bull_at_16) == 1
