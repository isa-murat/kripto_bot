"""Application configuration: env-backed secrets + YAML-backed settings."""

from __future__ import annotations

from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"


class EnvSettings(BaseSettings):
    """Secrets and environment-specific values. Sourced from `.env` / shell env."""

    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_testnet: bool = False

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    log_level: str = "INFO"
    log_file: str = "logs/bot.log"

    paper_initial_equity: float = 10_000.0
    paper_risk_per_trade: float = 0.01
    paper_max_concurrent_positions: int = 2

    db_path: str = "data/kripto_bot.db"
    ohlcv_data_dir: str = "data/ohlcv"


class ExchangeConfig(BaseModel):
    name: str = "binance"
    market_type: Literal["spot", "usdm", "coinm"] = "usdm"


class TimeframesConfig(BaseModel):
    entry: str = "5m"
    bias: str = "1h"
    trigger: str = "1m"


class DataConfig(BaseModel):
    source: Literal["websocket", "rest_polling"] = "rest_polling"
    poll_delay_seconds: int = 5
    hot_buffer_size: int = 500
    history_start: str = "2025-11-01"
    parquet_partition: Literal["daily", "monthly"] = "daily"


class SchedulerConfig(BaseModel):
    daily_report_time: str = "23:00"
    reconnect_backoff_initial: float = 1.0
    reconnect_backoff_max: float = 60.0


class TelegramConfig(BaseModel):
    rate_limit_per_second: int = 1
    send_daily_summary: bool = True
    send_signals: bool = True
    send_entries: bool = True
    send_exits: bool = True


class PaperConfig(BaseModel):
    fee_taker: float = 0.0004
    fee_maker: float = 0.0002
    slippage_ticks: int = 1
    max_concurrent_positions: int = 3
    risk_per_trade_pct: float = 0.01
    # PENDING setup'ları en fazla bu kadar entry-TF bar fill için bekler.
    # 24 × 5m = 120 dk default. run8 loglarında %61 expire görüldü;
    # ICT setup'ları çoğunlukla 1-2 saatte retest aldığı için TTL gevşetildi.
    # Price-based invalidation (cancel_invalidated_pending) yanlış kalanları
    # zaten süpürüyor, geniş TTL valid setup'ları kesmiyor.
    pending_ttl_bars: int = 24


class KillzoneConfig(BaseModel):
    """Killzone (session) window. Times are in UTC — convention shift away
    from prior TR-based config so London/NY DST does not corrupt definitions
    (Turkey has no DST). `enabled=False` lets you keep a zone defined but off."""
    name: str
    start_utc: str   # HH:MM UTC, inclusive
    end_utc: str     # HH:MM UTC, exclusive
    enabled: bool = True

    def start_time(self) -> time:
        h, m = (int(x) for x in self.start_utc.split(":"))
        return time(h, m)

    def end_time(self) -> time:
        h, m = (int(x) for x in self.end_utc.split(":"))
        return time(h, m)


class RegimeFilterConfig(BaseModel):
    """4h HTF regime gate before signal dispatch.

    Defaults follow the multi_run1 finding (ranging produced 77% of loss while
    trending was roughly break-even). 'unknown' (insufficient HTF bars) is
    treated as tradeable so warm-up doesn't suppress every trade.
    """
    enabled: bool = True
    exclude_ranging: bool = True
    exclude_trending: bool = False
    htf_timeframe: Literal["4h", "1h", "1d"] = "4h"
    classification_lookback: int = 20


class FiltersConfig(BaseModel):
    cooldown_minutes: int = 30
    killzones: list[KillzoneConfig] = Field(default_factory=list)
    regime_filter: RegimeFilterConfig = Field(default_factory=RegimeFilterConfig)


class LoggingConfig(BaseModel):
    level: str = "INFO"
    rotation: str = "100 MB"
    retention: str = "14 days"
    format: str = (
        "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | "
        "{name}:{function}:{line} | {message}"
    )


class Settings(BaseModel):
    """Top-level settings loaded from settings.yaml."""

    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    symbols: list[str] = Field(default_factory=list)
    timeframes: TimeframesConfig = Field(default_factory=TimeframesConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    paper: PaperConfig = Field(default_factory=PaperConfig)
    filters: FiltersConfig = Field(default_factory=FiltersConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


class StrategyParams(BaseModel):
    """Strategy tuning parameters loaded from strategy_params.yaml.

    Schema is intentionally permissive (dict) at this stage; concrete typed
    models will be added in Faz 1 as each module is implemented.
    """

    structure: dict = Field(default_factory=dict)
    poi: dict = Field(default_factory=dict)
    liquidity: dict = Field(default_factory=dict)
    bias: dict = Field(default_factory=dict)
    setup_sweep_fvg: dict = Field(default_factory=dict)
    risk: dict = Field(default_factory=dict)


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def get_env() -> EnvSettings:
    return EnvSettings()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(**_load_yaml(CONFIG_DIR / "settings.yaml"))


@lru_cache(maxsize=1)
def get_strategy_params() -> StrategyParams:
    return StrategyParams(**_load_yaml(CONFIG_DIR / "strategy_params.yaml"))
