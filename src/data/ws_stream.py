"""Binance USDT-M futures WebSocket: combined kline streams.

Endpoint: wss://fstream.binance.com/stream?streams=<s1>/<s2>/...
Each stream: <symbol_lower>@kline_<interval>

We emit a `Bar` event ONLY when `k.x == True` (bar closed). On disconnect we
exponential-backoff and on reconnect we REST-backfill any gap, then resume.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

import websockets
from websockets.exceptions import ConnectionClosed

from src.config import get_settings
from src.data.exchange import fetch_ohlcv_with_retry, make_async_exchange
from src.data.ohlcv_cache import OHLCVCache, from_ccxt_rows
from src.utils.logging import logger
from src.utils.time import timeframe_to_seconds

WS_BASE = "wss://fstream.binance.com/stream"
MAX_STREAMS_PER_CONN = 200


@dataclass(frozen=True)
class Bar:
    symbol: str
    timeframe: str
    open_time: datetime  # UTC
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


BarHandler = Callable[[Bar], Awaitable[None]]


def _parse_kline(msg: dict) -> Bar:
    """Parse a Binance combined-stream kline payload into a Bar.

    Payload shape: {"stream": "btcusdt@kline_5m", "data": {"e":..., "k": {...}}}
    """
    k = msg["data"]["k"]
    return Bar(
        symbol=str(k["s"]),
        timeframe=str(k["i"]),
        open_time=datetime.fromtimestamp(int(k["t"]) / 1000, tz=timezone.utc),
        close_time=datetime.fromtimestamp(int(k["T"]) / 1000, tz=timezone.utc),
        open=float(k["o"]),
        high=float(k["h"]),
        low=float(k["l"]),
        close=float(k["c"]),
        volume=float(k["v"]),
    )


def build_streams(symbols: list[str], timeframes: list[str]) -> list[str]:
    return [f"{s.lower()}@kline_{tf}" for s in symbols for tf in timeframes]


async def _backfill_gap(cache: OHLCVCache, symbol: str, tf: str) -> int:
    """REST-fetch missing bars between disk's last_ts and now. Returns count."""
    last = cache.last_ts(symbol, tf)
    if last is None:
        return 0
    tf_secs = timeframe_to_seconds(tf)
    since_ms = int(last.timestamp() * 1000) + tf_secs * 1000
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    if since_ms >= now_ms - tf_secs * 1000:
        return 0  # nothing meaningful to backfill yet
    ex = make_async_exchange()
    try:
        rows = await fetch_ohlcv_with_retry(ex, symbol, tf, since_ms=since_ms, limit=1500)
        if not rows:
            return 0
        df = from_ccxt_rows(rows)
        cache.append_bars(symbol, tf, df)
        cache.upsert_to_disk(symbol, tf, df)
        logger.info("Gap-fill {} {}: {} bars", symbol, tf, df.height)
        return df.height
    finally:
        await ex.close()


async def stream_klines(
    symbols: list[str],
    timeframes: list[str],
    on_bar_close: BarHandler,
    cache: OHLCVCache | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Connect, dispatch closed bars to `on_bar_close`. Reconnects forever
    (until `stop_event` is set) with exponential backoff + REST gap fill.
    """
    settings = get_settings()
    backoff = settings.scheduler.reconnect_backoff_initial
    backoff_max = settings.scheduler.reconnect_backoff_max

    streams = build_streams(symbols, timeframes)
    if len(streams) > MAX_STREAMS_PER_CONN:
        raise ValueError(
            f"Too many streams for one connection: {len(streams)} > {MAX_STREAMS_PER_CONN}. "
            "Split symbols across multiple ws connections."
        )
    url = f"{WS_BASE}?streams={'/'.join(streams)}"
    logger.info("WS connecting | streams={} | url=...?streams={} streams", len(streams), len(streams))

    while stop_event is None or not stop_event.is_set():
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                logger.info("WS connected")
                if cache is not None:
                    for s in symbols:
                        for tf in timeframes:
                            await _backfill_gap(cache, s, tf)
                backoff = settings.scheduler.reconnect_backoff_initial

                async for raw in ws:
                    msg = json.loads(raw)
                    if "data" not in msg or "k" not in msg["data"]:
                        continue
                    if not msg["data"]["k"].get("x"):
                        continue  # bar not closed yet
                    bar = _parse_kline(msg)
                    try:
                        await on_bar_close(bar)
                    except Exception as e:
                        logger.exception("on_bar_close handler error: {}", e)
        except (ConnectionClosed, OSError) as e:
            logger.warning("WS disconnected: {} | retry in {:.1f}s", e, backoff)
        except Exception as e:
            logger.exception("WS unexpected error: {} | retry in {:.1f}s", e, backoff)

        if stop_event is not None and stop_event.is_set():
            break
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, backoff_max)

    logger.info("WS stream stopped")
