"""Historical OHLCV downloader (REST). Used for backtest data prep.

CLI:
    python -m src.data.downloader --symbol BTCUSDT --tf 5m --from 2025-11-01
    python -m src.data.downloader --all   # all configured symbols × {entry, bias} TFs
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import polars as pl
import typer

from src.config import get_settings
from src.data.exchange import fetch_ohlcv_with_retry, make_async_exchange
from src.data.ohlcv_cache import OHLCVCache, from_ccxt_rows, merge_dedupe
from src.utils.logging import logger, setup_logging
from src.utils.time import timeframe_to_seconds

app = typer.Typer(help="Download historical OHLCV from Binance USDT-M futures.")

CHUNK_LIMIT = 1500  # Binance futures max bars per kline call


def _parse_iso_date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


async def download_one(
    symbol: str,
    tf: str,
    from_dt: datetime,
    to_dt: datetime,
) -> int:
    """Download [from_dt, to_dt) for (symbol, tf), upsert into Parquet. Returns total bars."""
    cache = OHLCVCache()
    ex = make_async_exchange()
    try:
        await ex.load_markets()

        tf_secs = timeframe_to_seconds(tf)
        chunk_ms = CHUNK_LIMIT * tf_secs * 1000
        cursor_ms = int(from_dt.timestamp() * 1000)
        end_ms = int(to_dt.timestamp() * 1000)

        all_chunks = []
        chunk_num = 0
        while cursor_ms < end_ms:
            chunk_num += 1
            rows = await fetch_ohlcv_with_retry(
                ex, symbol, tf, since_ms=cursor_ms, limit=CHUNK_LIMIT
            )
            if not rows:
                logger.warning("Empty chunk at cursor={}, stopping", cursor_ms)
                break
            df = from_ccxt_rows(rows)
            all_chunks.append(df)
            last_bar_ms = int(rows[-1][0])
            logger.info(
                "{} {} chunk {} | {} bars | last={}",
                symbol, tf, chunk_num, len(rows),
                datetime.fromtimestamp(last_bar_ms / 1000, tz=timezone.utc),
            )
            # Advance cursor past last fetched bar to avoid re-fetching it.
            # Don't break on `len(rows) < CHUNK_LIMIT` — Binance/CCXT often
            # caps responses at 1000 even when 1500 is requested, which would
            # falsely terminate the loop after a single chunk. We rely on
            # `cursor_ms >= end_ms` (loop guard) and `not rows` (empty
            # response) to exit cleanly.
            cursor_ms = last_bar_ms + tf_secs * 1000

        merged = merge_dedupe(*all_chunks) if all_chunks else from_ccxt_rows([])
        if merged.height > 0:
            merged = merged.filter(
                (pl.col("ts") >= from_dt) & (pl.col("ts") < to_dt)
            )
        return cache.upsert_to_disk(symbol, tf, merged)
    finally:
        await ex.close()


@app.command()
def download(
    symbol: str = typer.Option(..., help="e.g. BTCUSDT"),
    tf: str = typer.Option("5m", help="timeframe: 1m, 5m, 1h, ..."),
    from_: str = typer.Option(..., "--from", help="ISO date YYYY-MM-DD (UTC)"),
    to: str | None = typer.Option(None, help="ISO date YYYY-MM-DD (default: now UTC)"),
) -> None:
    setup_logging()
    from_dt = _parse_iso_date(from_)
    to_dt = _parse_iso_date(to) if to else datetime.now(tz=timezone.utc)
    total = asyncio.run(download_one(symbol, tf, from_dt, to_dt))
    typer.echo(f"Done. {symbol} {tf} total bars on disk: {total}")


@app.command("all")
def download_all(
    from_: str | None = typer.Option(None, "--from", help="ISO date (default: settings.data.history_start)"),
    to: str | None = typer.Option(None, help="ISO date (default: now)"),
) -> None:
    """Download all configured symbols × {entry, bias} timeframes."""
    setup_logging()
    settings = get_settings()
    from_dt = _parse_iso_date(from_ or settings.data.history_start)
    to_dt = _parse_iso_date(to) if to else datetime.now(tz=timezone.utc)

    async def runner() -> None:
        for symbol in settings.symbols:
            for tf in (settings.timeframes.entry, settings.timeframes.bias):
                logger.info("=== {} {} ===", symbol, tf)
                await download_one(symbol, tf, from_dt, to_dt)

    asyncio.run(runner())
    typer.echo("All downloads complete.")


if __name__ == "__main__":
    app()
