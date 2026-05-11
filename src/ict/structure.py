"""ICT Market Structure: swing high/low, BOS, CHoCH, MSS detection.

Pure functions over Polars OHLCV DataFrames; no I/O, no state.

Look-ahead bias guard
---------------------
A fractal swing at bar index `k` can only be confirmed once `k + lookback`
bars have closed (you need to see the right shoulder). Each `Swing` carries
`confirmed_at_index` so that downstream consumers (BOS / CHoCH detection)
do not use a swing as a reference before it could have been seen in real
time. In live trading this means: never use a swing whose `confirmed_at_index`
is greater than the index of the bar you are currently evaluating.

Glossary (see docs/memory_bank/glossary.md):
    BOS  — Break of Structure: trend yönünde son swing kırıldı (devam sinyali)
    CHoCH — Change of Character: trend tersine son swing kırıldı (dönüş ihtimali)
    MSS  — Market Structure Shift: CHoCH + displacement (güçlü mum) ile onaylanmış
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

import polars as pl


class SwingType(str, Enum):
    HIGH = "high"
    LOW = "low"


class Trend(str, Enum):
    BULL = "bull"
    BEAR = "bear"
    NEUTRAL = "neutral"


class EventType(str, Enum):
    BOS = "bos"
    CHOCH = "choch"
    MSS = "mss"


@dataclass(frozen=True)
class Swing:
    index: int                 # bar index in the source DataFrame
    confirmed_at_index: int    # earliest bar at which this swing could be observed live
    ts: datetime               # open_time of the swing bar (UTC)
    price: float               # high (for SwingType.HIGH) or low (SwingType.LOW)
    type: SwingType


@dataclass(frozen=True)
class StructureEvent:
    index: int                 # bar index that produced the break
    ts: datetime               # open_time of the breaking bar (UTC)
    type: EventType
    direction: Trend           # BULL = upward break, BEAR = downward break
    broken_swing: Swing        # the swing that was broken
    body_size: float           # |close - open| of the breaking bar
    atr: float                 # ATR at the breaking bar (for displacement check)


# ----------------------------- swing detection ------------------------------ #


def find_swings(df: pl.DataFrame, lookback: int = 3) -> list[Swing]:
    """Detect fractal swing highs and lows.

    A bar at index `i` is a swing high iff its high is strictly greater than
    every high in `[i-lookback, i-1]` AND `[i+1, i+lookback]`. Symmetric for
    lows. A bar that qualifies as a high is not also tested as a low.

    Returns swings in chronological order. Each swing has
    `confirmed_at_index = index + lookback` (the bar at which the right
    shoulder closes).
    """
    n = df.height
    if n < 2 * lookback + 1:
        return []

    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    ts = df["ts"].to_list()

    swings: list[Swing] = []
    for i in range(lookback, n - lookback):
        h = highs[i]
        is_high = True
        for k in range(1, lookback + 1):
            if highs[i - k] >= h or highs[i + k] >= h:
                is_high = False
                break
        if is_high:
            swings.append(Swing(
                index=i,
                confirmed_at_index=i + lookback,
                ts=ts[i],
                price=float(h),
                type=SwingType.HIGH,
            ))
            continue  # a single bar cannot be both high and low swing

        l = lows[i]
        is_low = True
        for k in range(1, lookback + 1):
            if lows[i - k] <= l or lows[i + k] <= l:
                is_low = False
                break
        if is_low:
            swings.append(Swing(
                index=i,
                confirmed_at_index=i + lookback,
                ts=ts[i],
                price=float(l),
                type=SwingType.LOW,
            ))

    return swings


# --------------------------------- ATR -------------------------------------- #


def compute_atr(df: pl.DataFrame, period: int = 14) -> pl.Series:
    """Average True Range as a simple moving average of TR.

    First `period - 1` values are null. Returned series length matches df.
    """
    if df.height < 2:
        return pl.Series("atr", [None] * df.height, dtype=pl.Float64)
    tr = (
        df.lazy()
        .with_columns(pl.col("close").shift(1).alias("_prev_close"))
        .with_columns([
            (pl.col("high") - pl.col("low")).alias("_hl"),
            (pl.col("high") - pl.col("_prev_close")).abs().alias("_hc"),
            (pl.col("low") - pl.col("_prev_close")).abs().alias("_lc"),
        ])
        .with_columns(pl.max_horizontal("_hl", "_hc", "_lc").alias("_tr"))
        .select("_tr")
        .collect()["_tr"]
    )
    return tr.rolling_mean(window_size=period).rename("atr")


# ---------------------------- structure events ------------------------------ #


def detect_events(
    df: pl.DataFrame,
    swings: list[Swing],
    atr: pl.Series | None = None,
    displacement_atr_mult: float = 1.5,
) -> list[StructureEvent]:
    """Walk bars in order and emit BOS / CHoCH / MSS events.

    Rules:
        - Maintain `last_swing_high` and `last_swing_low` references. Each is
          updated only when the corresponding swing has been confirmed
          (`bar_index >= swing.confirmed_at_index`).
        - At each bar, if `bar.high > last_swing_high.price` → bullish break.
          If `bar.low  < last_swing_low.price`               → bearish break.
        - Bullish break in BULL trend → BOS. In BEAR trend → CHoCH (trend flips).
          In NEUTRAL → BOS (first direction observed).
        - A CHoCH whose breaking bar's body exceeds `displacement_atr_mult × ATR`
          is upgraded to MSS.
        - Once a swing is broken it is cleared; the next confirmed swing of the
          same type becomes the new reference.

    Bullish and bearish checks are evaluated independently on each bar; if a
    single bar happens to break both, the bullish event is emitted first.
    """
    if not swings or df.height == 0:
        return []

    if atr is None:
        atr = compute_atr(df)
    atr_arr = atr.to_numpy()

    opens = df["open"].to_numpy()
    closes = df["close"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    ts = df["ts"].to_list()

    events: list[StructureEvent] = []
    trend: Trend = Trend.NEUTRAL
    last_swing_high: Swing | None = None
    last_swing_low: Swing | None = None

    swing_idx = 0  # cursor into swings list

    for i in range(df.height):
        # Promote any swings that became live-observable on or before this bar
        while swing_idx < len(swings) and swings[swing_idx].confirmed_at_index <= i:
            s = swings[swing_idx]
            if s.type == SwingType.HIGH:
                last_swing_high = s
            else:
                last_swing_low = s
            swing_idx += 1

        bar_open = opens[i]
        bar_close = closes[i]
        body = abs(float(bar_close) - float(bar_open))
        cur_atr = float(atr_arr[i]) if i < len(atr_arr) and atr_arr[i] is not None and atr_arr[i] == atr_arr[i] else 0.0

        # Bullish break
        if last_swing_high is not None and float(highs[i]) > last_swing_high.price:
            etype = _classify_break(trend, bullish=True)
            if etype == EventType.CHOCH and cur_atr > 0 and body > displacement_atr_mult * cur_atr:
                etype = EventType.MSS
            events.append(StructureEvent(
                index=i, ts=ts[i], type=etype, direction=Trend.BULL,
                broken_swing=last_swing_high, body_size=body, atr=cur_atr,
            ))
            trend = Trend.BULL
            last_swing_high = None
            continue

        # Bearish break
        if last_swing_low is not None and float(lows[i]) < last_swing_low.price:
            etype = _classify_break(trend, bullish=False)
            if etype == EventType.CHOCH and cur_atr > 0 and body > displacement_atr_mult * cur_atr:
                etype = EventType.MSS
            events.append(StructureEvent(
                index=i, ts=ts[i], type=etype, direction=Trend.BEAR,
                broken_swing=last_swing_low, body_size=body, atr=cur_atr,
            ))
            trend = Trend.BEAR
            last_swing_low = None

    return events


def _classify_break(trend: Trend, *, bullish: bool) -> EventType:
    if trend == Trend.NEUTRAL:
        return EventType.BOS
    if (bullish and trend == Trend.BULL) or (not bullish and trend == Trend.BEAR):
        return EventType.BOS
    return EventType.CHOCH


def current_trend(events: list[StructureEvent]) -> Trend:
    """The trend direction implied by the last structure event."""
    if not events:
        return Trend.NEUTRAL
    return events[-1].direction
