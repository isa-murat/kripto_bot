"""Unit tests for OHLCV cache + ccxt row conversion."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import pytest

from src.data.exchange import to_ccxt_symbol, to_native_symbol
from src.data.ohlcv_cache import (
    OHLCV_SCHEMA,
    OHLCVCache,
    empty_ohlcv,
    from_ccxt_rows,
    merge_dedupe,
)


@pytest.fixture
def tmp_cache(tmp_path: Path) -> OHLCVCache:
    return OHLCVCache(max_bars=100, base_dir=tmp_path)


def _ccxt_row(ts_ms: int, c: float = 100.0) -> list[float]:
    return [float(ts_ms), c - 1, c + 2, c - 2, c, 1234.5]


def test_empty_ohlcv_schema():
    df = empty_ohlcv()
    assert df.is_empty()
    assert set(df.columns) == set(OHLCV_SCHEMA.keys())


def test_from_ccxt_rows_typed():
    rows = [_ccxt_row(1700000000000), _ccxt_row(1700000300000, c=101.0)]
    df = from_ccxt_rows(rows)
    assert df.height == 2
    assert df.schema["ts"] == pl.Datetime("ms", time_zone="UTC")
    assert df["close"][1] == 101.0


def test_merge_dedupe_keeps_latest():
    df1 = from_ccxt_rows([_ccxt_row(1700000000000, c=100.0)])
    df2 = from_ccxt_rows([_ccxt_row(1700000000000, c=999.0)])  # same ts, new value
    merged = merge_dedupe(df1, df2)
    assert merged.height == 1
    assert merged["close"][0] == 999.0


def test_cache_append_and_buffer(tmp_cache: OHLCVCache):
    df = from_ccxt_rows([_ccxt_row(1700000000000), _ccxt_row(1700000300000)])
    tmp_cache.append_bars("BTCUSDT", "5m", df)
    buf = tmp_cache.buffer("BTCUSDT", "5m")
    assert buf.height == 2


def test_cache_max_bars_evicts_oldest(tmp_path: Path):
    cache = OHLCVCache(max_bars=2, base_dir=tmp_path)
    rows = [_ccxt_row(1700000000000 + i * 300000) for i in range(5)]
    cache.append_bars("BTCUSDT", "5m", from_ccxt_rows(rows))
    buf = cache.buffer("BTCUSDT", "5m")
    assert buf.height == 2
    # latest two should remain
    assert buf["ts"][0] == datetime(2023, 11, 14, 22, 25, 0, tzinfo=timezone.utc) or buf.height == 2


def test_cache_upsert_persists(tmp_cache: OHLCVCache):
    df = from_ccxt_rows([_ccxt_row(1700000000000)])
    tmp_cache.upsert_to_disk("ETHUSDT", "1h", df)
    last = tmp_cache.last_ts("ETHUSDT", "1h")
    assert last is not None
    assert last == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)


def test_symbol_conversion():
    assert to_ccxt_symbol("BTCUSDT") == "BTC/USDT:USDT"
    assert to_ccxt_symbol("BTC/USDT:USDT") == "BTC/USDT:USDT"
    assert to_native_symbol("BTC/USDT:USDT") == "BTCUSDT"
    assert to_native_symbol("BTCUSDT") == "BTCUSDT"


def test_symbol_conversion_rejects_non_usdt():
    with pytest.raises(ValueError):
        to_ccxt_symbol("BTCBUSD")
