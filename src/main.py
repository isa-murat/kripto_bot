"""Entry point for the live (paper) bot.

Faz 0 finish:
    - Loads config + sets up logging
    - Initializes OHLCV cache (loads existing parquet)
    - Connects to Binance WS for all (symbol × timeframe) combinations
    - On each closed bar: appends to cache + logs + (optionally) Telegram
    - Sends a "hello" to Telegram on startup
"""

from __future__ import annotations

import asyncio
import signal

import typer

from src.config import get_env, get_settings, get_strategy_params
from src.data.ohlcv_cache import OHLCVCache
from src.data.rest_poller import rest_poll_loop
from src.data.ws_stream import Bar, stream_klines
from src.engine.paper_broker import PaperBroker, Position
from src.engine.position_mgr import PositionManager
from src.engine.scheduler import JobScheduler
from src.engine.signal_router import SignalRouter
from src.notify.telegram import (
    TelegramNotifier,
    format_position_closed,
    format_position_filled,
)
from src.strategies.sweep_fvg import SetupParams, TradeSignal
from src.strategies.sweep_fvg import evaluate as evaluate_sweep_fvg
from src.utils.logging import logger, setup_logging

# Minimum bar count required before the strategy will fire. Covers ATR(14)
# warmup + a few swings on each side. Below this, evaluate() is skipped for
# the symbol/tf to avoid noisy false signals during cache warmup.
MIN_BARS_FOR_STRATEGY = 60

app = typer.Typer(help="Run the kripto_bot live (paper) loop.")


async def _run() -> None:
    setup_logging()
    settings = get_settings()
    env = get_env()
    strategy_params = get_strategy_params()
    setup_params = SetupParams.from_strategy_params(strategy_params)

    logger.info("=" * 60)
    logger.info("kripto_bot starting")
    logger.info("Exchange: {} ({})", settings.exchange.name, settings.exchange.market_type)
    logger.info("Symbols: {}", ", ".join(settings.symbols))
    logger.info(
        "Timeframes: entry={} bias={} trigger={}",
        settings.timeframes.entry,
        settings.timeframes.bias,
        settings.timeframes.trigger,
    )
    logger.info("Paper initial equity: {}", env.paper_initial_equity)
    logger.info("=" * 60)

    cache = OHLCVCache()
    for symbol in settings.symbols:
        for tf in (settings.timeframes.entry, settings.timeframes.bias, settings.timeframes.trigger):
            cache.load_from_disk(symbol, tf)

    notifier: TelegramNotifier | None = None
    if env.telegram_bot_token and env.telegram_chat_id:
        notifier = TelegramNotifier.from_env()
        await notifier.start()
        await notifier.send(
            f"🚀 kripto_bot started\nSymbols: {', '.join(settings.symbols)}\n"
            f"TFs: {settings.timeframes.entry}/{settings.timeframes.bias}/{settings.timeframes.trigger}"
        )
    else:
        logger.warning("Telegram disabled (token/chat_id missing in .env)")

    # Paper broker + position manager
    broker = PaperBroker()
    logger.info(
        "PaperBroker ready | equity={:.2f} | open={} pending={} closed={}",
        broker.equity, len(broker.open_positions),
        len(broker.pending_positions), len(broker.closed_trades),
    )

    # Signal router: cooldown / dedup / duplicate / max_concurrent + dispatch.
    # Provider callbacks query broker on every check → no state drift.
    def _active_positions():
        return [*broker.pending_positions, *broker.open_positions]

    router = SignalRouter(
        cooldown_minutes=settings.filters.cooldown_minutes,
        max_concurrent=settings.paper.max_concurrent_positions,
        active_count_provider=broker.total_active,
        active_positions_provider=_active_positions,
    )

    if notifier is not None:
        router.add_handler(notifier.signal_handler)

    async def _paper_handler(signal: TradeSignal) -> None:
        broker.open_from_signal(signal)

    router.add_handler(_paper_handler)

    async def _on_position_filled(pos: Position) -> None:
        if notifier is not None:
            await notifier.send(format_position_filled(pos))

    async def _on_position_closed(pos: Position) -> None:
        if notifier is not None:
            await notifier.send(format_position_closed(pos))

    position_mgr = PositionManager(
        broker=broker,
        on_filled=_on_position_filled,
        on_closed=_on_position_closed,
    )

    # Scheduler: günlük 23:00 TR raporu (TelegramNotifier yoksa noop)
    job_scheduler = JobScheduler(broker=broker, notifier=notifier)
    job_scheduler.start()

    logger.info(
        "SignalRouter ready | cooldown={}min | max_concurrent={} | handlers={}",
        settings.filters.cooldown_minutes,
        settings.paper.max_concurrent_positions,
        len(router._handlers) if hasattr(router, "_handlers") else 0,
    )

    async def on_bar_close(bar: Bar) -> None:
        cache.append_bar(
            bar.symbol, bar.timeframe,
            ts=bar.open_time,
            open_=bar.open, high=bar.high, low=bar.low, close=bar.close,
            volume=bar.volume,
        )
        # Persist closed bars from the entry timeframe (most strategy-critical)
        if bar.timeframe == settings.timeframes.entry:
            import polars as pl
            single = pl.DataFrame(
                {
                    "ts": [bar.open_time],
                    "open": [bar.open], "high": [bar.high],
                    "low": [bar.low], "close": [bar.close],
                    "volume": [bar.volume],
                }
            ).with_columns(pl.col("ts").cast(pl.Datetime("ms", time_zone="UTC")))
            cache.upsert_to_disk(bar.symbol, bar.timeframe, single)

        logger.info(
            "Bar | {} {} | open_time={} close={} vol={}",
            bar.symbol, bar.timeframe, bar.open_time.isoformat(), bar.close, bar.volume,
        )

        # Drive paper engine: fill pendings + check SL/TP on every entry-TF bar
        if bar.timeframe == settings.timeframes.entry:
            try:
                await position_mgr.on_bar(
                    symbol=bar.symbol,
                    bar_high=bar.high,
                    bar_low=bar.low,
                    bar_ts=bar.close_time,
                )
            except Exception as e:
                logger.exception("PositionManager.on_bar error | {} | {}", bar.symbol, e)

        # Strategy evaluation runs on entry-timeframe bar closes only.
        if bar.timeframe != settings.timeframes.entry:
            return
        await _evaluate_and_dispatch(bar.symbol)

    async def _evaluate_and_dispatch(symbol: str) -> None:
        df_ltf = cache.buffer(symbol, settings.timeframes.entry)
        df_htf = cache.buffer(symbol, settings.timeframes.bias)
        if df_ltf.height < MIN_BARS_FOR_STRATEGY or df_htf.height < MIN_BARS_FOR_STRATEGY:
            return
        try:
            signal = evaluate_sweep_fvg(
                symbol=symbol,
                df_ltf=df_ltf,
                df_htf=df_htf,
                params=setup_params,
            )
        except Exception as e:
            logger.exception("Strategy evaluate error | {} | {}", symbol, e)
            return
        if signal is None:
            return
        try:
            await router.submit(signal)
        except Exception as e:
            logger.exception("SignalRouter submit error | {} | {}", symbol, e)

    stop_event = asyncio.Event()

    def _request_stop(*_):
        logger.info("Shutdown signal received")
        stop_event.set()

    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _request_stop)
            except (ValueError, OSError):
                pass  # not always available (e.g. non-main thread on Windows)
    except Exception:
        pass

    timeframes = list({
        settings.timeframes.entry,
        settings.timeframes.bias,
        settings.timeframes.trigger,
    })

    try:
        if settings.data.source == "rest_polling":
            logger.info("Live data source: REST polling (delay={}s)", settings.data.poll_delay_seconds)
            await rest_poll_loop(
                symbols=settings.symbols,
                timeframes=timeframes,
                on_bar_close=on_bar_close,
                cache=cache,
                stop_event=stop_event,
                poll_delay_seconds=settings.data.poll_delay_seconds,
            )
        else:
            logger.info("Live data source: WebSocket")
            await stream_klines(
                symbols=settings.symbols,
                timeframes=timeframes,
                on_bar_close=on_bar_close,
                cache=cache,
                stop_event=stop_event,
            )
    finally:
        try:
            job_scheduler.stop()
        except Exception as e:
            logger.exception("Scheduler stop error: {}", e)
        if notifier is not None:
            await notifier.send("🛑 kripto_bot stopped")
            await notifier.stop()
        logger.info("Bye.")


@app.command()
def run() -> None:
    """Run the live paper bot."""
    asyncio.run(_run())


@app.command()
def check() -> None:
    """Sanity check: load config + log it, no network."""
    setup_logging()
    settings = get_settings()
    env = get_env()
    logger.info("Config OK")
    logger.info("Symbols: {}", settings.symbols)
    logger.info(
        "Killzones (UTC): {}",
        [(kz.name, kz.start_utc, kz.end_utc, kz.enabled) for kz in settings.filters.killzones],
    )
    logger.info("Telegram configured: {}", bool(env.telegram_bot_token and env.telegram_chat_id))
    logger.info("Binance API key present: {}", bool(env.binance_api_key))


if __name__ == "__main__":
    app()
