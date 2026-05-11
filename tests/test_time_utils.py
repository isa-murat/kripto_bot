"""Unit tests for src.utils.time."""

from __future__ import annotations

from datetime import datetime, timezone

from src.config import KillzoneConfig
from src.utils.time import (
    floor_to_timeframe,
    in_killzone,
    timeframe_to_seconds,
    to_tr,
    to_utc,
)


def test_to_tr_offset():
    dt_utc = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)
    dt_tr = to_tr(dt_utc)
    assert dt_tr.hour == 15  # UTC+3


def test_roundtrip_utc_tr():
    dt_utc = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)
    assert to_utc(to_tr(dt_utc)) == dt_utc


def test_floor_to_5m():
    dt = datetime(2026, 5, 11, 12, 7, 33, tzinfo=timezone.utc)
    floored = floor_to_timeframe(dt, "5m")
    assert floored == datetime(2026, 5, 11, 12, 5, 0, tzinfo=timezone.utc)


def test_floor_to_1h():
    dt = datetime(2026, 5, 11, 12, 59, 59, tzinfo=timezone.utc)
    floored = floor_to_timeframe(dt, "1h")
    assert floored == datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)


def test_timeframe_to_seconds():
    assert timeframe_to_seconds("1m") == 60
    assert timeframe_to_seconds("5m") == 300
    assert timeframe_to_seconds("1h") == 3600


def test_killzone_london_active():
    # 07:30 UTC, London KZ aktif (07:00-10:00 UTC)
    dt_utc = datetime(2026, 5, 11, 7, 30, tzinfo=timezone.utc)
    kzs = [
        KillzoneConfig(name="london", start_utc="07:00", end_utc="10:00"),
        KillzoneConfig(name="new_york", start_utc="12:00", end_utc="15:00"),
    ]
    active = in_killzone(dt_utc, kzs)
    assert active is not None
    assert active.name == "london"


def test_killzone_outside():
    # 05:00 UTC, hiçbir KZ aktif değil (London 07:00'da başlar)
    dt_utc = datetime(2026, 5, 11, 5, 0, tzinfo=timezone.utc)
    kzs = [
        KillzoneConfig(name="london", start_utc="07:00", end_utc="10:00"),
        KillzoneConfig(name="new_york", start_utc="12:00", end_utc="15:00"),
    ]
    assert in_killzone(dt_utc, kzs) is None


def test_killzone_disabled_skipped():
    """A killzone with enabled=False must never be returned even if the time matches."""
    dt_utc = datetime(2026, 5, 11, 2, 0, tzinfo=timezone.utc)
    kzs = [
        KillzoneConfig(name="asia", start_utc="00:00", end_utc="04:00", enabled=False),
        KillzoneConfig(name="london", start_utc="07:00", end_utc="10:00", enabled=True),
    ]
    assert in_killzone(dt_utc, kzs) is None


def test_killzone_naive_datetime_treated_as_utc():
    """Defensive: naive (tzinfo=None) datetimes assumed UTC."""
    dt_naive = datetime(2026, 5, 11, 7, 30)
    kzs = [KillzoneConfig(name="london", start_utc="07:00", end_utc="10:00")]
    active = in_killzone(dt_naive, kzs)
    assert active is not None and active.name == "london"
