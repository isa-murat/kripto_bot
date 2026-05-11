"""ICT Liquidity: pool detection (BSL/SSL, equal H/L) + sweep recognition.

Concepts (see docs/memory_bank/glossary.md):
    BSL — Buy-Side Liquidity. Swing-high level above which buy stops accumulate.
    SSL — Sell-Side Liquidity. Swing-low level below which sell stops accumulate.
    Equal H/L — Two or more swings at near-identical prices form a strong
                magnet (more stops cluster there).
    Sweep — A bar whose wick pierces a liquidity pool but whose body closes
            back inside it. Suggests stops were taken and a reversal may follow.

All functions are pure: input is a Polars OHLCV DataFrame and a precomputed
swing list (from `src.ict.structure.find_swings`); output is typed dataclasses.

Look-ahead bias guard
---------------------
A pool's `confirmed_at_index` is set to the latest member swing's
`confirmed_at_index`, so live consumers can filter out pools that aren't yet
observable at the bar they are evaluating.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

import polars as pl

from src.ict.structure import Swing, SwingType, Trend


class PoolType(str, Enum):
    BSL = "bsl"
    SSL = "ssl"


@dataclass(frozen=True)
class LiquidityPool:
    type: PoolType
    price: float                       # BSL: max(member prices); SSL: min(member prices)
    swings: tuple[Swing, ...]          # member swings, sorted by index
    created_at_index: int              # earliest member swing index
    confirmed_at_index: int            # earliest bar at which the whole pool is observable

    @property
    def is_equal(self) -> bool:
        """True iff 2+ swings cluster at near-identical prices (strong magnet)."""
        return len(self.swings) >= 2


@dataclass(frozen=True)
class Sweep:
    bar_index: int
    ts: datetime
    pool: LiquidityPool
    wick_size: float                   # wick distance beyond the pool level
    body_size: float                   # |open - close|

    @property
    def expected_reaction(self) -> Trend:
        """BSL swept → bearish reaction; SSL swept → bullish reaction."""
        return Trend.BEAR if self.pool.type == PoolType.BSL else Trend.BULL


# ----------------------------- pool detection ------------------------------- #


def find_pools(
    swings: list[Swing],
    tolerance_price: float,
    min_count: int = 1,
) -> list[LiquidityPool]:
    """Group same-type swings whose prices lie within `tolerance_price`.

    `tolerance_price` is an absolute price distance (caller computes it as
    `tolerance_atr_mult × ATR` for the symbol/timeframe in question — keeps
    this function unit-test friendly without coupling to ATR computation).

    `min_count`:
        1 → every swing becomes a pool of size 1 (default — useful as
            single-shot liquidity reference)
        2 → only equal-H/L clusters survive (real "equal highs/lows" pools)

    Returned list is sorted by `created_at_index` (chronological).
    """
    if not swings:
        return []

    highs = [s for s in swings if s.type == SwingType.HIGH]
    lows = [s for s in swings if s.type == SwingType.LOW]

    pools: list[LiquidityPool] = []
    pools.extend(_group_swings(highs, PoolType.BSL, tolerance_price, min_count))
    pools.extend(_group_swings(lows, PoolType.SSL, tolerance_price, min_count))
    pools.sort(key=lambda p: p.created_at_index)
    return pools


def _group_swings(
    swings: list[Swing],
    pool_type: PoolType,
    tolerance: float,
    min_count: int,
) -> list[LiquidityPool]:
    if not swings:
        return []

    by_price = sorted(swings, key=lambda s: s.price)
    clusters: list[list[Swing]] = [[by_price[0]]]
    for s in by_price[1:]:
        if s.price - clusters[-1][-1].price <= tolerance:
            clusters[-1].append(s)
        else:
            clusters.append([s])

    pools: list[LiquidityPool] = []
    for cluster in clusters:
        if len(cluster) < min_count:
            continue
        prices = [s.price for s in cluster]
        pool_price = max(prices) if pool_type == PoolType.BSL else min(prices)
        by_index = sorted(cluster, key=lambda s: s.index)
        pools.append(LiquidityPool(
            type=pool_type,
            price=pool_price,
            swings=tuple(by_index),
            created_at_index=by_index[0].index,
            confirmed_at_index=by_index[-1].confirmed_at_index,
        ))
    return pools


def active_pools_at(
    pools: list[LiquidityPool],
    bar_index: int,
    min_age_bars: int = 0,
) -> list[LiquidityPool]:
    """Filter pools observable as of `bar_index` (live-safe).

    `min_age_bars`: drop pools confirmed less than this many bars ago — a
    just-formed pool isn't a real "magnet" yet, more stops accumulate over
    time. 0 (default) preserves the original behaviour.
    """
    return [
        p for p in pools
        if p.confirmed_at_index <= bar_index - min_age_bars
    ]


# ------------------------------- sweep detection ---------------------------- #


def detect_sweep(
    df: pl.DataFrame,
    bar_index: int,
    pools: list[LiquidityPool],
    min_wick_pct: float = 0.30,
) -> Sweep | None:
    """Return a `Sweep` if the bar at `bar_index` sweeps any pool, else None.

    Rules:
        BSL sweep: bar.high > pool.price AND bar.close <= pool.price
                   AND (high - pool.price) / (high - low) >= min_wick_pct
        SSL sweep: bar.low  < pool.price AND bar.close >= pool.price
                   AND (pool.price - low)  / (high - low) >= min_wick_pct

    Only pools whose `confirmed_at_index <= bar_index` are considered. If a bar
    sweeps multiple pools, the one with the largest wick is returned (most
    aggressive sweep wins).
    """
    if bar_index < 0 or bar_index >= df.height or not pools:
        return None

    o = float(df["open"][bar_index])
    h = float(df["high"][bar_index])
    lo = float(df["low"][bar_index])
    c = float(df["close"][bar_index])
    ts = df["ts"][bar_index]
    bar_range = h - lo
    if bar_range <= 0:
        return None
    body = abs(o - c)

    candidates: list[Sweep] = []
    for pool in pools:
        if pool.confirmed_at_index > bar_index:
            continue
        if pool.type == PoolType.BSL:
            # Wick must pierce the pool from below. Strict ICT close-back-inside
            # is rare; we accept (a) close inside, OR (b) close outside but
            # wick clearly dominates the body (manipulation pattern).
            if h > pool.price:
                wick = h - pool.price
                if wick / bar_range < min_wick_pct:
                    continue
                close_inside = c <= pool.price
                wick_dominant = wick > body
                if close_inside or wick_dominant:
                    candidates.append(Sweep(
                        bar_index=bar_index, ts=ts, pool=pool,
                        wick_size=wick, body_size=body,
                    ))
        else:  # SSL
            if lo < pool.price:
                wick = pool.price - lo
                if wick / bar_range < min_wick_pct:
                    continue
                close_inside = c >= pool.price
                wick_dominant = wick > body
                if close_inside or wick_dominant:
                    candidates.append(Sweep(
                        bar_index=bar_index, ts=ts, pool=pool,
                        wick_size=wick, body_size=body,
                    ))

    if not candidates:
        return None
    candidates.sort(key=lambda s: s.wick_size, reverse=True)
    return candidates[0]
