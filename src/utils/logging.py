"""Loguru-based logger setup. Call `setup_logging()` once at app start."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from src.config import REPO_ROOT, get_env, get_settings


def setup_logging() -> None:
    env = get_env()
    settings = get_settings()
    log_cfg = settings.logging

    logger.remove()

    logger.add(
        sys.stderr,
        level=env.log_level or log_cfg.level,
        format=log_cfg.format,
        colorize=True,
        backtrace=True,
        diagnose=False,
    )

    log_path = REPO_ROOT / env.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        str(log_path),
        level=log_cfg.level,
        format=log_cfg.format,
        rotation=log_cfg.rotation,
        retention=log_cfg.retention,
        encoding="utf-8",
        enqueue=True,
    )

    logger.info("Logger initialized | level={} | file={}", log_cfg.level, log_path)


__all__ = ["setup_logging", "logger"]
