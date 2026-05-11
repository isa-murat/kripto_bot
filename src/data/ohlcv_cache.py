"""OHLCV cache: hot RAM buffer (Polars) + cold Parquet storage.

One Parquet file per (symbol, timeframe). Reads merge disk + RAM, writes
upsert into the file (dedup by `ts`, keep latest).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import polars as pl

from src.config import REPO_ROOT, get_env, get_settings
from src.utils.logging import logger

OHLCV_SCHEMA: dict[str, pl.DataType] = {
    "ts": pl.Datetime("ms", time_zone="UTC"),
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
}


def empty_ohlcv() -> pl.DataFrame:
    return pl.DataFrame(schema=OHLCV_SCHEMA)


def from_ccxt_rows(rows: list[list[float]]) -> pl.DataFrame:
    """Convert raw ccxt OHLCV [ts_ms, o, h, l, c, v] to typed Polars frame."""
    if not rows:
        return empty_ohlcv()
    return pl.DataFrame(
        {
            "ts": [int(r[0]) for r in rows],
            "open": [float(r[1]) for r in rows],
            "high": [float(r[2]) for r in rows],
            "low": [float(r[3]) for r in rows],
            "close": [float(r[4]) for r in rows],
            "volume": [float(r[5]) for r in rows],
        }
    ).with_columns(pl.col("ts").cast(pl.Datetime("ms", time_zone="UTC")))


def merge_dedupe(*frames: pl.DataFrame) -> pl.DataFrame:
    parts = [f for f in frames if f.height > 0]
    if not parts:
        return empty_ohlcv()
    return (
        pl.concat(parts, how="vertical_relaxed")
        .unique(subset=["ts"], keep="last")
        .sort("ts")
    )


class OHLCVCache:
    """Per-(symbol, tf) hot buffer with disk persistence.

    The disk file is the source of truth for history; the RAM buffer holds the
    most recent `max_bars` for fast strategy access.
    """

    def __init__(self, max_bars: int | None = None, base_dir: Path | None = None):
        settings = get_settings()
        env = get_env()
        self._max_bars = max_bars if max_bars is not None else settings.data.hot_buffer_size
        self._base_dir = base_dir if base_dir is not None else REPO_ROOT / env.ohlcv_data_dir
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._buffers: dict[tuple[str, str], pl.DataFrame] = {}

    def _path(self, symbol: str, tf: str) -> Path:
        return self._base_dir / f"{symbol}_{tf}.parquet"

    def _key(self, symbol: str, tf: str) -> tuple[str, str]:
        return (symbol, tf)

    def load_from_disk(self, symbol: str, tf: str) -> pl.DataFrame:
        path = self._path(symbol, tf)
        if not path.exists():
            return empty_ohlcv()
        try:
            df = pl.read_parquet(path)
            if df.height > self._max_bars:
                df = df.tail(self._max_bars)
            self._buffers[self._key(symbol, tf)] = df
            logger.debug("Loaded {} bars from {}", df.height, path.name)
            return df
        except Exception as e:
            logger.error("Failed to load {}: {}", path, e)
            return empty_ohlcv()

    def buffer(self, symbol: str, tf: str) -> pl.DataFrame:
        key = self._key(symbol, tf)
        if key not in self._buffers:
            return self.load_from_disk(symbol, tf)
        return self._buffers[key]

    def append_bars(self, symbol: str, tf: str, new_bars: pl.DataFrame) -> pl.DataFrame:
        if new_bars.is_empty():
            return self.buffer(symbol, tf)
        key = self._key(symbol, tf)
        existing = self._buffers.get(key, empty_ohlcv())
        merged = merge_dedupe(existing, new_bars)
        if merged.height > self._max_bars:
            merged = merged.tail(self._max_bars)
        self._buffers[key] = merged
        return merged

    def append_bar(
        self,
        symbol: str,
        tf: str,
        ts: datetime,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float,
    ) -> pl.DataFrame:
        bar_df = pl.DataFrame(
            {
                "ts": [ts],
                "open": [open_],
                "high": [high],
                "low": [low],
                "close": [close],
                "volume": [volume],
            }
        ).with_columns(pl.col("ts").cast(pl.Datetime("ms", time_zone="UTC")))
        return self.append_bars(symbol, tf, bar_df)

    def upsert_to_disk(self, symbol: str, tf: str, new_bars: pl.DataFrame) -> int:
        """Merge `new_bars` into the on-disk Parquet, dedup by ts. Returns row count."""
        if new_bars.is_empty():
            return 0
        path = self._path(symbol, tf)
        existing = pl.read_parquet(path) if path.exists() else empty_ohlcv()
        merged = merge_dedupe(existing, new_bars)
        merged.write_parquet(path)
        logger.info("Upserted {} bars -> {} (total: {})", new_bars.height, path.name, merged.height)
        return merged.height

    def last_ts(self, symbol: str, tf: str) -> datetime | None:
        path = self._path(symbol, tf)
        if not path.exists():
            return None
        df = pl.read_parquet(path, columns=["ts"])
        if df.is_empty():
            return None
        return df["ts"].max()
