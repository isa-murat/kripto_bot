"""ICT Points of Interest: Fair Value Gaps (FVG) and Order Blocks (OB).

A POI is a price zone where ICT expects strong reaction on retest — used as
entry trigger for the Sweep+FVG setup (and others).

Concepts (see docs/memory_bank/glossary.md):
    FVG (Fair Value Gap, a.k.a. Imbalance)
        3-bar pattern: candle[i-1], candle[i], candle[i+1]
        Bullish FVG: candle[i-1].high < candle[i+1].low
            zone = (top: candle[i+1].low, bottom: candle[i-1].high)
        Bearish FVG: candle[i-1].low  > candle[i+1].high
            zone = (top: candle[i-1].low, bottom: candle[i+1].high)
        Confirmed at bar i+1 (the right shoulder).

    OB (Order Block)
        The last opposite-coloured candle preceding a displacement move.
        Bullish OB: last bearish (close<open) candle before a bullish
                    displacement (body > displacement_atr_mult × ATR).
        Bearish OB: mirror.

All functions are pure: input is a Polars OHLCV DataFrame, output is typed
dataclasses with `confirmed_at_index` so live consumers can avoid look-ahead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import polars as pl

from src.ict.structure import Trend, compute_atr


@dataclass(frozen=True)
class FVG:
    direction: Trend                  # BULL or BEAR
    middle_index: int                 # bar i (the gap-creating bar)
    middle_ts: datetime
    top: float                        # upper edge of the gap
    bottom: float                     # lower edge of the gap
    confirmed_at_index: int           # = middle_index + 1 (right-shoulder bar)

    @property
    def size(self) -> float:
        return self.top - self.bottom

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2.0


@dataclass(frozen=True)
class OrderBlock:
    direction: Trend                  # BULL = expects upside reaction; BEAR = downside
    index: int                        # OB candle's index
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    confirmed_at_index: int           # bar of the displacement that confirmed this OB

    @property
    def midpoint(self) -> float:
        return (self.high + self.low) / 2.0


# --------------------------------- FVG -------------------------------------- #


def find_fvgs(
    df: pl.DataFrame,
    atr: pl.Series | None = None,
    min_atr_mult: float = 0.15,
) -> list[FVG]:
    """Detect bullish and bearish 3-bar Fair Value Gaps.

    `min_atr_mult`: gaps smaller than `min_atr_mult × ATR(at bar i)` are
    considered noise and skipped. ATR is computed if not provided.
    """
    if df.height < 3:
        return []

    if atr is None:
        atr = compute_atr(df)

    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    ts = df["ts"].to_list()
    atr_arr = atr.to_numpy()

    fvgs: list[FVG] = []
    for i in range(1, df.height - 1):
        bar_atr = _safe_float(atr_arr[i] if i < len(atr_arr) else None)
        min_size = min_atr_mult * bar_atr if bar_atr > 0 else 0.0

        prev_high = float(highs[i - 1])
        prev_low = float(lows[i - 1])
        next_high = float(highs[i + 1])
        next_low = float(lows[i + 1])

        # Bullish FVG: gap upward between bar[i-1].high and bar[i+1].low
        if prev_high < next_low:
            gap = next_low - prev_high
            if gap > min_size:
                fvgs.append(FVG(
                    direction=Trend.BULL,
                    middle_index=i, middle_ts=ts[i],
                    top=next_low, bottom=prev_high,
                    confirmed_at_index=i + 1,
                ))
            continue

        # Bearish FVG: gap downward between bar[i+1].high and bar[i-1].low
        if prev_low > next_high:
            gap = prev_low - next_high
            if gap > min_size:
                fvgs.append(FVG(
                    direction=Trend.BEAR,
                    middle_index=i, middle_ts=ts[i],
                    top=prev_low, bottom=next_high,
                    confirmed_at_index=i + 1,
                ))

    return fvgs


def active_fvgs_at(
    fvgs: list[FVG],
    bar_index: int,
    max_age_bars: int = 30,
) -> list[FVG]:
    """FVGs that are observable and within freshness window at `bar_index`."""
    return [
        f for f in fvgs
        if f.confirmed_at_index <= bar_index
        and (bar_index - f.middle_index) <= max_age_bars
    ]


def is_fvg_mitigated(df: pl.DataFrame, fvg: FVG, until_index: int) -> bool:
    """True iff price traded back through any part of the FVG between
    `confirmed_at_index + 1` and `until_index` (inclusive).

    For bullish FVG mitigation: any bar.low <= fvg.top AND bar.high >= fvg.bottom
    (price entered the zone). Symmetric for bearish.
    """
    start = fvg.confirmed_at_index + 1
    end = min(until_index, df.height - 1)
    if start > end:
        return False
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    for i in range(start, end + 1):
        if float(lows[i]) <= fvg.top and float(highs[i]) >= fvg.bottom:
            return True
    return False


# ------------------------------- Order Block -------------------------------- #


def find_order_blocks(
    df: pl.DataFrame,
    atr: pl.Series | None = None,
    displacement_atr_mult: float = 1.5,
    lookback: int = 10,
) -> list[OrderBlock]:
    """Detect Order Blocks preceding a displacement bar.

    Algorithm:
        For each bar i with `body > displacement_atr_mult × ATR(i)`:
            - Direction = bullish if close>open else bearish
            - Walk back up to `lookback` bars; first opposite-coloured candle
              is the OB. Each (j, direction) is emitted at most once.
    """
    if df.height < 2:
        return []

    if atr is None:
        atr = compute_atr(df)

    opens = df["open"].to_numpy()
    closes = df["close"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    ts = df["ts"].to_list()
    atr_arr = atr.to_numpy()

    obs: list[OrderBlock] = []
    seen: set[tuple[int, Trend]] = set()

    for i in range(1, df.height):
        bar_atr = _safe_float(atr_arr[i] if i < len(atr_arr) else None)
        if bar_atr <= 0:
            continue
        body = abs(float(closes[i]) - float(opens[i]))
        if body <= displacement_atr_mult * bar_atr:
            continue

        bullish_disp = float(closes[i]) > float(opens[i])
        ob_direction = Trend.BULL if bullish_disp else Trend.BEAR

        for k in range(1, min(lookback, i) + 1):
            j = i - k
            j_open = float(opens[j])
            j_close = float(closes[j])
            is_bear_candle = j_close < j_open
            is_bull_candle = j_close > j_open

            opposite = (bullish_disp and is_bear_candle) or (not bullish_disp and is_bull_candle)
            if not opposite:
                continue

            key = (j, ob_direction)
            if key in seen:
                break
            seen.add(key)
            obs.append(OrderBlock(
                direction=ob_direction,
                index=j, ts=ts[j],
                open=j_open, high=float(highs[j]),
                low=float(lows[j]), close=j_close,
                confirmed_at_index=i,
            ))
            break

    return obs


# --------------------------------- helpers ---------------------------------- #


def _safe_float(value) -> float:
    """Return float(value), or 0.0 for None / NaN."""
    if value is None:
        return 0.0
    f = float(value)
    if f != f:  # NaN check
        return 0.0
    return f
