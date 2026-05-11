"""Smoke tests for src.backtest.runner — replay loop without crashing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import pytest

from src.backtest.runner import _build_htf_lookup, run_backtest
from src.strategies.sweep_fvg import SetupParams

UTC = timezone.utc


def _bars(n: int, *, start: datetime, minutes_per_bar: int,
          base_price: float = 100.0) -> pl.DataFrame:
    return pl.DataFrame({
        "ts": [start + timedelta(minutes=minutes_per_bar * i) for i in range(n)],
        "open":   [base_price] * n,
        "high":   [base_price + 1] * n,
        "low":    [base_price - 1] * n,
        "close":  [base_price] * n,
        "volume": [100.0] * n,
    }).with_columns(pl.col("ts").cast(pl.Datetime("ms", time_zone="UTC")))


# ============================== HTF lookup ================================== #


def test_htf_lookup_returns_minus_one_before_first_close():
    """LTF bar earlier than the first HTF close → -1 (no HTF available)."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    ltf = _bars(3, start=start, minutes_per_bar=5)                # 00:00, 00:05, 00:10
    htf = _bars(2, start=start, minutes_per_bar=60)               # 00:00, 01:00 (closes at 01:00, 02:00)
    lookup = _build_htf_lookup(ltf, htf, "1h")
    # All three LTF bars are before 01:00 (first HTF close), so all -1
    assert lookup == [-1, -1, -1]


def test_htf_lookup_advances_after_close():
    """LTF bar after the first HTF close → 0 (first HTF bar usable)."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    htf = _bars(2, start=start, minutes_per_bar=60)               # 00:00, 01:00
    # LTF bar at 01:05 → HTF[0] (00:00 close at 01:00) is now usable
    ltf_ts = [start + timedelta(minutes=65)]
    ltf = pl.DataFrame({
        "ts": ltf_ts,
        "open": [100.0], "high": [101.0], "low": [99.0],
        "close": [100.0], "volume": [100.0],
    }).with_columns(pl.col("ts").cast(pl.Datetime("ms", time_zone="UTC")))
    lookup = _build_htf_lookup(ltf, htf, "1h")
    assert lookup == [0]


# ============================== run_backtest =============================== #


@pytest.mark.asyncio
async def test_run_backtest_raises_when_data_missing(tmp_path: Path, monkeypatch):
    """No Parquet on disk → FileNotFoundError surfaces clearly."""
    monkeypatch.setenv("OHLCV_DATA_DIR", str(tmp_path))
    # Reset cached env
    from src.config import get_env
    get_env.cache_clear()
    with pytest.raises(FileNotFoundError):
        await run_backtest(
            symbol="DOESNOTEXIST",
            from_dt=datetime(2026, 1, 1, tzinfo=UTC),
            to_dt=datetime(2026, 2, 1, tzinfo=UTC),
        )
    get_env.cache_clear()


@pytest.mark.asyncio
async def test_run_backtest_flat_data_produces_no_signals(tmp_path: Path, monkeypatch):
    """Flat OHLCV → no swings → no bias → no signals → empty trade log."""
    monkeypatch.setenv("OHLCV_DATA_DIR", str(tmp_path))
    from src.config import get_env
    get_env.cache_clear()

    start = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    # 200 LTF bars (5m), 200 HTF bars (1h) — well above MIN_BARS_FOR_STRATEGY
    ltf = _bars(200, start=start, minutes_per_bar=5)
    htf = _bars(200, start=start, minutes_per_bar=60)

    (tmp_path / "TESTUSDT_5m.parquet").write_bytes(b"")
    ltf.write_parquet(tmp_path / "TESTUSDT_5m.parquet")
    htf.write_parquet(tmp_path / "TESTUSDT_1h.parquet")

    params = SetupParams(require_killzone=False)
    metrics, broker = await run_backtest(
        symbol="TESTUSDT",
        from_dt=start,
        to_dt=start + timedelta(days=20),
        params=params,
        initial_equity=10_000.0,
    )
    assert metrics.total_trades == 0
    assert broker.equity == pytest.approx(10_000.0)

    get_env.cache_clear()
