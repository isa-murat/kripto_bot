"""REST-based bar polling — drop-in replacement for ws_stream when WS is blocked.

Loops every minute (after a small delay past the minute boundary) and, for each
timeframe whose bar just closed, fetches the latest bars via REST and emits any
not-yet-seen closed bar to the same `on_bar_close` callback as the WS path.

This is the MVP live source while Binance Futures WebSocket is region-filtered
on this network (see ADR-0009).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from src.data.exchange import fetch_ohlcv_with_retry, make_async_exchange
from src.data.ohlcv_cache import OHLCVCache
from src.data.ws_stream import Bar, BarHandler  # reuse the dataclass + type
from src.utils.logging import logger
from src.utils.time import timeframe_to_seconds

UTC = timezone.utc


def _bar_due_now(tf: str, now: datetime) -> bool:
    """True if `now` is at or just past a bar-close boundary for `tf`.

    For tf <= 1m: always true (poll every cycle).
    For tf > 1m: true when minutes-since-epoch is divisible by tf-in-minutes.
    """
    tf_secs = timeframe_to_seconds(tf)
    if tf_secs <= 60:
        return True
    epoch_mins = int(now.timestamp() // 60)
    return epoch_mins % (tf_secs // 60) == 0


async def _wait_until_next_minute(stop_event: asyncio.Event, delay_secs: int) -> bool:
    """Sleep until next minute boundary + delay_secs. Returns True if stop requested."""
    now = datetime.now(tz=UTC)
    next_minute = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    target = next_minute + timedelta(seconds=delay_secs)
    sleep_for = (target - now).total_seconds()
    if sleep_for <= 0:
        return False
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=sleep_for)
        return True
    except asyncio.TimeoutError:
        return False


async def rest_poll_loop(
    symbols: list[str],
    timeframes: list[str],
    on_bar_close: BarHandler,
    cache: OHLCVCache | None = None,
    stop_event: asyncio.Event | None = None,
    poll_delay_seconds: int = 5,
) -> None:
    """Poll Binance REST every minute and emit newly-closed bars.

    Idempotent: tracks `last_seen` per (symbol, tf) so the same bar is never
    emitted twice. Initialized from cache disk state if available.
    """
    if stop_event is None:
        stop_event = asyncio.Event()

    last_seen: dict[tuple[str, str], datetime | None] = {
        (s, tf): None for s in symbols for tf in timeframes
    }
    if cache is not None:
        for s in symbols:
            for tf in timeframes:
                ts = cache.last_ts(s, tf)
                if ts is not None:
                    last_seen[(s, tf)] = ts

    ex = make_async_exchange()
    try:
        await ex.load_markets()
        logger.info(
            "REST poller started | symbols={} | tfs={} | poll_delay={}s",
            symbols, timeframes, poll_delay_seconds,
        )

        while not stop_event.is_set():
            stopped = await _wait_until_next_minute(stop_event, poll_delay_seconds)
            if stopped:
                break

            now = datetime.now(tz=UTC)
            due_tfs = [tf for tf in timeframes if _bar_due_now(tf, now)]
            if not due_tfs:
                continue

            for tf in due_tfs:
                tf_secs = timeframe_to_seconds(tf)
                for symbol in symbols:
                    try:
                        rows = await fetch_ohlcv_with_retry(ex, symbol, tf, limit=3)
                    except Exception as e:
                        logger.error("REST poll fetch failed | {} {} | {}", symbol, tf, e)
                        continue

                    new_count = 0
                    for row in rows:
                        ts_ms = int(row[0])
                        bar_open = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
                        bar_close = bar_open + timedelta(seconds=tf_secs)
                        if bar_close > now:
                            continue  # still-forming bar
                        prev = last_seen[(symbol, tf)]
                        if prev is not None and bar_open <= prev:
                            continue
                        bar = Bar(
                            symbol=symbol,
                            timeframe=tf,
                            open_time=bar_open,
                            close_time=bar_close,
                            open=float(row[1]),
                            high=float(row[2]),
                            low=float(row[3]),
                            close=float(row[4]),
                            volume=float(row[5]),
                        )
                        try:
                            await on_bar_close(bar)
                        except Exception as e:
                            logger.exception("on_bar_close error | {} {} | {}", symbol, tf, e)
                        last_seen[(symbol, tf)] = bar_open
                        new_count += 1

                    if new_count > 0:
                        logger.debug("REST {} {} | {} new bar(s)", symbol, tf, new_count)
    finally:
        await ex.close()
        logger.info("REST poller stopped")
