"""Unit tests for src.ict.liquidity."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import polars as pl
import pytest

from src.ict.liquidity import (
    LiquidityPool,
    PoolType,
    Sweep,
    active_pools_at,
    detect_sweep,
    find_pools,
)
from src.ict.structure import Swing, SwingType, Trend

UTC = timezone.utc


def _ts(i: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=5 * i)


def _swing(idx: int, price: float, type_: SwingType, lookback: int = 3) -> Swing:
    return Swing(
        index=idx,
        confirmed_at_index=idx + lookback,
        ts=_ts(idx),
        price=price,
        type=type_,
    )


def _bars(rows: list[tuple[float, float, float, float]]) -> pl.DataFrame:
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


# ============================== find_pools ================================== #


def test_find_pools_empty_input():
    assert find_pools([], tolerance_price=1.0) == []


def test_find_pools_single_swing_becomes_pool_when_min_count_1():
    swings = [_swing(3, 100.0, SwingType.HIGH)]
    pools = find_pools(swings, tolerance_price=0.5, min_count=1)
    assert len(pools) == 1
    assert pools[0].type == PoolType.BSL
    assert pools[0].price == 100.0
    assert not pools[0].is_equal


def test_find_pools_groups_equal_highs():
    """Three nearby swing highs form a single BSL pool with the max price."""
    swings = [
        _swing(3, 100.00, SwingType.HIGH),
        _swing(10, 100.05, SwingType.HIGH),
        _swing(20, 99.95, SwingType.HIGH),
    ]
    pools = find_pools(swings, tolerance_price=0.5, min_count=1)
    bsl = [p for p in pools if p.type == PoolType.BSL]
    assert len(bsl) == 1
    pool = bsl[0]
    assert pool.is_equal
    assert pool.price == 100.05
    assert len(pool.swings) == 3
    assert pool.created_at_index == 3
    assert pool.confirmed_at_index == 23  # last swing's confirmed_at


def test_find_pools_groups_equal_lows_returns_min_price():
    swings = [
        _swing(5, 50.00, SwingType.LOW),
        _swing(15, 49.95, SwingType.LOW),
    ]
    pools = find_pools(swings, tolerance_price=0.2, min_count=1)
    ssl = [p for p in pools if p.type == PoolType.SSL]
    assert len(ssl) == 1
    assert ssl[0].price == 49.95  # min for SSL


def test_find_pools_separates_distant_swings():
    swings = [
        _swing(3, 100.0, SwingType.HIGH),
        _swing(10, 200.0, SwingType.HIGH),
    ]
    pools = find_pools(swings, tolerance_price=10.0, min_count=1)
    bsl = [p for p in pools if p.type == PoolType.BSL]
    assert len(bsl) == 2


def test_find_pools_min_count_filters_singles():
    swings = [
        _swing(3, 100.0, SwingType.HIGH),
        _swing(10, 200.0, SwingType.HIGH),
    ]
    pools = find_pools(swings, tolerance_price=10.0, min_count=2)
    assert pools == []


def test_find_pools_separates_high_and_low_pools():
    swings = [
        _swing(3, 100.0, SwingType.HIGH),
        _swing(10, 50.0, SwingType.LOW),
    ]
    pools = find_pools(swings, tolerance_price=1.0, min_count=1)
    types = sorted(p.type for p in pools)
    assert types == [PoolType.BSL, PoolType.SSL]


def test_find_pools_chronological_order():
    swings = [
        _swing(20, 100.0, SwingType.HIGH),
        _swing(5, 50.0, SwingType.LOW),
        _swing(10, 200.0, SwingType.HIGH),
    ]
    pools = find_pools(swings, tolerance_price=1.0, min_count=1)
    indices = [p.created_at_index for p in pools]
    assert indices == sorted(indices)


# ============================ active_pools_at =============================== #


def test_active_pools_at_filters_unconfirmed():
    s_old = _swing(2, 100.0, SwingType.HIGH)            # confirmed_at = 5
    s_new = _swing(20, 200.0, SwingType.HIGH)           # confirmed_at = 23
    pools = find_pools([s_old, s_new], tolerance_price=1.0)
    assert len(pools) == 2
    active = active_pools_at(pools, bar_index=10)
    assert len(active) == 1
    assert active[0].price == 100.0


# ============================== detect_sweep ================================ #


def test_detect_bullish_sweep_bsl():
    """High pierces BSL, body closes below — bullish (high) sweep."""
    # bar: open=100 high=110 low=99 close=99.5 → wick=5, range=11, 0.45 > 0.30
    df = _bars([(100, 110, 99, 99.5)])
    pool = LiquidityPool(
        type=PoolType.BSL, price=105,
        swings=(_swing(0, 105, SwingType.HIGH),),
        created_at_index=0, confirmed_at_index=0,
    )
    sweep = detect_sweep(df, bar_index=0, pools=[pool], min_wick_pct=0.30)
    assert sweep is not None
    assert sweep.pool.price == 105
    assert sweep.expected_reaction == Trend.BEAR
    assert sweep.wick_size == 5.0


def test_detect_bearish_sweep_ssl():
    """Low pierces SSL, body closes above — bearish (low) sweep."""
    df = _bars([(100, 101, 90, 100.5)])  # wick=5, range=11
    pool = LiquidityPool(
        type=PoolType.SSL, price=95,
        swings=(_swing(0, 95, SwingType.LOW),),
        created_at_index=0, confirmed_at_index=0,
    )
    sweep = detect_sweep(df, bar_index=0, pools=[pool], min_wick_pct=0.30)
    assert sweep is not None
    assert sweep.pool.price == 95
    assert sweep.expected_reaction == Trend.BULL


def test_no_sweep_when_close_beyond_pool():
    """Bar high goes above pool but closes above too → continuation, not sweep."""
    df = _bars([(100, 110, 99, 108)])
    pool = LiquidityPool(
        type=PoolType.BSL, price=105,
        swings=(_swing(0, 105, SwingType.HIGH),),
        created_at_index=0, confirmed_at_index=0,
    )
    assert detect_sweep(df, bar_index=0, pools=[pool]) is None


def test_no_sweep_when_wick_too_small():
    """Wick crosses pool but is < min_wick_pct of bar range."""
    df = _bars([(100, 106, 99, 99)])  # wick=1 (106-105), range=7 → 0.14 < 0.30
    pool = LiquidityPool(
        type=PoolType.BSL, price=105,
        swings=(_swing(0, 105, SwingType.HIGH),),
        created_at_index=0, confirmed_at_index=0,
    )
    assert detect_sweep(df, bar_index=0, pools=[pool], min_wick_pct=0.30) is None


def test_pool_ignored_when_not_yet_confirmed():
    """Live-safety: bar_index < pool.confirmed_at_index → pool not seen."""
    df = _bars([(100, 110, 99, 99.5)] * 5)
    pool = LiquidityPool(
        type=PoolType.BSL, price=105,
        swings=(_swing(7, 105, SwingType.HIGH),),
        created_at_index=7, confirmed_at_index=10,
    )
    assert detect_sweep(df, bar_index=3, pools=[pool]) is None


def test_largest_wick_wins_when_multiple_pools_swept():
    """If a bar sweeps multiple BSL pools, the one with biggest wick is returned."""
    # bar: open=100 high=120 low=99 close=99.5
    df = _bars([(100, 120, 99, 99.5)])
    p_near = LiquidityPool(
        type=PoolType.BSL, price=110,
        swings=(_swing(0, 110, SwingType.HIGH),),
        created_at_index=0, confirmed_at_index=0,
    )
    p_far = LiquidityPool(
        type=PoolType.BSL, price=105,  # smaller pool, bigger wick (120 - 105 = 15)
        swings=(_swing(0, 105, SwingType.HIGH),),
        created_at_index=0, confirmed_at_index=0,
    )
    sweep = detect_sweep(df, bar_index=0, pools=[p_near, p_far])
    assert sweep is not None
    assert sweep.pool.price == 105   # larger wick wins
    assert sweep.wick_size == 15.0


def test_zero_range_bar_returns_none():
    """Doji where high == low → bar_range = 0, sweep undefined."""
    df = _bars([(100, 100, 100, 100)])
    pool = LiquidityPool(
        type=PoolType.BSL, price=99,
        swings=(_swing(0, 99, SwingType.HIGH),),
        created_at_index=0, confirmed_at_index=0,
    )
    assert detect_sweep(df, bar_index=0, pools=[pool]) is None
