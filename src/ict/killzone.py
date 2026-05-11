"""Killzone (session) filter — wraps `utils.time.in_killzone` with config wiring.

Implementation pending — Faz 1.
"""

from __future__ import annotations

from datetime import datetime

from src.config import get_settings
from src.utils.time import in_killzone


def is_active_killzone(dt_utc: datetime) -> bool:
    settings = get_settings()
    return in_killzone(dt_utc, settings.filters.killzones) is not None
