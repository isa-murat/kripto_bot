"""CCXT-based Binance USDT-M futures wrapper.

Symbol convention:
    - "Native" (Binance / WebSocket / project-internal): "BTCUSDT"
    - CCXT unified: "BTC/USDT:USDT"
"""

from __future__ import annotations

import asyncio
from typing import Any

import ccxt
import ccxt.async_support as ccxt_async

from src.config import get_env
from src.utils.logging import logger


def to_ccxt_symbol(native: str) -> str:
    """BTCUSDT -> BTC/USDT:USDT"""
    if "/" in native:
        return native
    if not native.endswith("USDT"):
        raise ValueError(f"Only USDT-margined symbols supported: {native}")
    base = native[:-4]
    return f"{base}/USDT:USDT"


def to_native_symbol(ccxt_symbol: str) -> str:
    """BTC/USDT:USDT -> BTCUSDT"""
    if "/" not in ccxt_symbol:
        return ccxt_symbol
    base = ccxt_symbol.split("/")[0]
    return f"{base}USDT"


def _common_options() -> dict[str, Any]:
    env = get_env()
    opts: dict[str, Any] = {
        "apiKey": env.binance_api_key or None,
        "secret": env.binance_api_secret or None,
        "enableRateLimit": True,
        "options": {
            "defaultType": "future",
            "adjustForTimeDifference": True,
        },
    }
    if env.binance_testnet:
        opts["urls"] = {
            "api": {
                "public": "https://testnet.binancefuture.com/fapi/v1",
                "private": "https://testnet.binancefuture.com/fapi/v1",
                "fapiPublic": "https://testnet.binancefuture.com/fapi/v1",
                "fapiPrivate": "https://testnet.binancefuture.com/fapi/v1",
            }
        }
    return opts


def make_sync_exchange() -> ccxt.binanceusdm:
    """Synchronous client. Use only for one-off scripts/tests."""
    return ccxt.binanceusdm(_common_options())


def make_async_exchange() -> ccxt_async.binanceusdm:
    """Async client. Caller is responsible for `await ex.close()`."""
    return ccxt_async.binanceusdm(_common_options())


async def fetch_ohlcv_with_retry(
    ex: ccxt_async.binanceusdm,
    symbol: str,
    timeframe: str,
    since_ms: int | None = None,
    limit: int = 1500,
    max_retries: int = 3,
    backoff_initial: float = 1.0,
) -> list[list[float]]:
    """Fetch OHLCV with exponential backoff on transient errors.

    Returns raw ccxt OHLCV: list of [ts_ms, open, high, low, close, volume].
    """
    delay = backoff_initial
    for attempt in range(1, max_retries + 1):
        try:
            return await ex.fetch_ohlcv(
                to_ccxt_symbol(symbol),
                timeframe=timeframe,
                since=since_ms,
                limit=limit,
            )
        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RequestTimeout) as e:
            if attempt == max_retries:
                logger.error(
                    "fetch_ohlcv failed after {} attempts: {} {} | {}",
                    attempt, symbol, timeframe, e,
                )
                raise
            logger.warning(
                "fetch_ohlcv retry {}/{} after {}s: {} {} | {}",
                attempt, max_retries, delay, symbol, timeframe, e,
            )
            await asyncio.sleep(delay)
            delay *= 2
    return []  # unreachable
