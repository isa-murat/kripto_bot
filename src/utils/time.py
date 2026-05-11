"""Time helpers — UTC ↔ TR (Turkey, UTC+3, no DST) and killzone checks.

Convention:
    - Internal storage and computation: ALWAYS UTC (timezone-aware).
    - Display / killzone definitions: TR local time (UTC+3).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from src.config import KillzoneConfig

TR_TZ = timezone(timedelta(hours=3))
UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_tr(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(TR_TZ)


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TR_TZ)
    return dt.astimezone(UTC)


def in_killzone(dt_utc: datetime, killzones: list[KillzoneConfig]) -> KillzoneConfig | None:
    """Return the active (enabled) killzone at `dt_utc`, or None.

    Killzones are defined in UTC; the disabled ones (`enabled=False`) are
    skipped so a zone can stay defined in config but turned off without
    deleting it.
    """
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=UTC)
    t = dt_utc.astimezone(UTC).time()
    for kz in killzones:
        if not kz.enabled:
            continue
        if _time_in_range(t, kz.start_time(), kz.end_time()):
            return kz
    return None


def _time_in_range(t: time, start: time, end: time) -> bool:
    """Inclusive on start, exclusive on end. Handles ranges crossing midnight."""
    if start <= end:
        return start <= t < end
    return t >= start or t < end


TIMEFRAME_TO_SECONDS: dict[str, int] = {
    "1m": 60,
    "3m": 3 * 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "2h": 2 * 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
}


def timeframe_to_seconds(tf: str) -> int:
    if tf not in TIMEFRAME_TO_SECONDS:
        raise ValueError(f"Unsupported timeframe: {tf}")
    return TIMEFRAME_TO_SECONDS[tf]


def floor_to_timeframe(dt: datetime, tf: str) -> datetime:
    """Floor a UTC datetime down to the start of its timeframe bucket."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    secs = timeframe_to_seconds(tf)
    epoch = int(dt.timestamp())
    floored = epoch - (epoch % secs)
    return datetime.fromtimestamp(floored, tz=UTC)
