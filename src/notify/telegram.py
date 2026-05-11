"""Telegram notifier: rate-limited async queue + simple text/markdown send.

Usage:
    notifier = TelegramNotifier.from_env()
    await notifier.start()
    await notifier.send("hello")
    ...
    await notifier.stop()

For strategy signals, use `format_signal_message(signal)` and either send
directly or register `notifier.signal_handler` with the SignalRouter.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

UTC = timezone.utc

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

from src.config import get_env, get_settings
from src.strategies.sweep_fvg import TradeSignal
from src.utils.logging import logger
from src.utils.time import to_tr


class TelegramNotifier:
    def __init__(
        self,
        token: str,
        chat_id: str,
        rate_limit_per_second: int = 1,
        queue_max: int = 1000,
    ):
        if not token or not chat_id:
            raise ValueError("Telegram token and chat_id required")
        self._bot: Bot = Bot(token=token)
        self._chat_id = chat_id
        self._min_interval = 1.0 / max(rate_limit_per_second, 1)
        self._queue: asyncio.Queue[tuple[str, Optional[str]]] = asyncio.Queue(maxsize=queue_max)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    @classmethod
    def from_env(cls) -> "TelegramNotifier":
        env = get_env()
        settings = get_settings()
        return cls(
            token=env.telegram_bot_token,
            chat_id=env.telegram_chat_id,
            rate_limit_per_second=settings.telegram.rate_limit_per_second,
        )

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._worker(), name="telegram-worker")
        try:
            me = await self._bot.get_me()
            logger.info("Telegram bot ready | @{} | chat_id={}", me.username, self._chat_id)
        except TelegramError as e:
            logger.error("Telegram getMe failed: {}", e)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._queue.put(("__stop__", None))
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None

    async def send(self, text: str, parse_mode: str | None = None) -> None:
        try:
            self._queue.put_nowait((text, parse_mode))
        except asyncio.QueueFull:
            logger.warning("Telegram queue full, dropping message")

    async def _worker(self) -> None:
        last_sent = 0.0
        while not self._stop.is_set():
            text, parse_mode = await self._queue.get()
            if text == "__stop__":
                break
            elapsed = time.monotonic() - last_sent
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            try:
                await self._bot.send_message(
                    chat_id=self._chat_id,
                    text=text,
                    parse_mode=ParseMode.MARKDOWN if parse_mode == "markdown" else None,
                )
                last_sent = time.monotonic()
            except TelegramError as e:
                logger.error("Telegram send failed: {} | dropping", e)
            except Exception as e:
                logger.exception("Telegram worker unexpected error: {}", e)

    # ---- SignalRouter handler ------------------------------------------- #

    async def signal_handler(self, signal: TradeSignal) -> None:
        """Handler signature for SignalRouter — formats and queues a signal."""
        await self.send(format_signal_message(signal))


def format_signal_message(signal: TradeSignal) -> str:
    """Render a TradeSignal as a human-readable Telegram message (TR time)."""
    side_label = "🟢 LONG" if signal.side.value == "bull" else "🔴 SHORT"
    risk = abs(signal.entry_price - signal.stop_loss)
    reward = abs(signal.take_profit - signal.entry_price)
    tr_time = to_tr(signal.ts).strftime("%Y-%m-%d %H:%M TR")
    meta = signal.meta or {}
    return (
        f"🎯 {signal.setup_name.upper()} SIGNAL\n"
        f"Symbol: {signal.symbol}\n"
        f"Side:   {side_label}\n"
        f"Entry:  {signal.entry_price:.4f}\n"
        f"SL:     {signal.stop_loss:.4f}  (risk {risk:.4f})\n"
        f"TP:     {signal.take_profit:.4f}  (reward {reward:.4f})\n"
        f"RR:     {signal.rr:.2f}\n"
        f"Sweep:  {meta.get('sweep_pool_price', '?')}  bar #{meta.get('sweep_bar_index', '?')}\n"
        f"MSS:    {meta.get('mss_event_type', '?')} bar #{meta.get('mss_bar_index', '?')}\n"
        f"FVG:    {meta.get('fvg_bottom', '?')} – {meta.get('fvg_top', '?')}\n"
        f"HTF:    trend={meta.get('htf_trend', '?')} zone={meta.get('htf_zone', '?')}\n"
        f"Time:   {tr_time}"
    )


def format_position_filled(pos) -> str:  # type: Position
    side_label = "🟢 LONG" if pos.side.value == "bull" else "🔴 SHORT"
    tr_time = to_tr(pos.filled_ts).strftime("%H:%M TR") if pos.filled_ts else "?"
    return (
        f"📥 ENTRY FILLED\n"
        f"{pos.symbol}  {side_label}\n"
        f"Fill:   {pos.fill_entry:.4f}\n"
        f"SL:     {pos.stop_loss:.4f}\n"
        f"TP:     {pos.take_profit:.4f}\n"
        f"Size:   {pos.size:.6f}\n"
        f"Risk:   {pos.risk_amount:.2f}\n"
        f"Fee:    {pos.entry_fee:.4f}\n"
        f"Time:   {tr_time}"
    )


def format_daily_report(broker, since: datetime | None = None) -> str:  # type: PaperBroker
    """Compose a daily summary of broker state.

    `since` filters the closed-trade window (defaults: last 24h in TR).
    """
    from src.config import get_env
    env = get_env()
    initial_eq = env.paper_initial_equity

    if since is None:
        since = to_tr(datetime.now(UTC) - timedelta(hours=24))
    since_utc = since.astimezone(UTC) if since.tzinfo else since.replace(tzinfo=UTC)

    closed = broker.closed_trades
    closed_24h = [t for t in closed if t.position.closed_ts and t.position.closed_ts >= since_utc]

    pnl_24h = sum(t.position.pnl for t in closed_24h)
    wins_24h = sum(1 for t in closed_24h if t.position.pnl > 0)
    losses_24h = sum(1 for t in closed_24h if t.position.pnl < 0)
    win_rate_24h = (wins_24h / max(wins_24h + losses_24h, 1)) * 100 if closed_24h else 0.0

    best = max(closed_24h, key=lambda t: t.position.pnl, default=None)
    worst = min(closed_24h, key=lambda t: t.position.pnl, default=None)

    open_lines = [
        f"  {p.symbol} {('🟢 LONG' if p.side.value == 'bull' else '🔴 SHORT')}"
        f"  fill={p.fill_entry:.4f} SL={p.stop_loss:.4f} TP={p.take_profit:.4f}"
        for p in broker.open_positions
    ]
    pending_lines = [
        f"  {p.symbol} {('🟢 LONG' if p.side.value == 'bull' else '🔴 SHORT')}"
        f"  entry={p.requested_entry:.4f}"
        for p in broker.pending_positions
    ]

    eq_pnl = broker.equity - initial_eq
    eq_pnl_pct = (eq_pnl / initial_eq) * 100 if initial_eq > 0 else 0.0
    now_tr = to_tr(datetime.now(UTC)).strftime("%Y-%m-%d %H:%M TR")

    lines = [
        "📊 GÜNLÜK RAPOR",
        now_tr,
        "",
        f"Equity: {broker.equity:.2f}  (lifetime {eq_pnl:+.2f}, {eq_pnl_pct:+.2f}%)",
        f"Toplam trade: {len(closed)} (all-time) | {len(closed_24h)} (24h)",
    ]
    if closed_24h:
        lines.append(f"Win rate (24h): {wins_24h}/{wins_24h + losses_24h} = {win_rate_24h:.0f}%")
        lines.append(f"P&L (24h): {pnl_24h:+.2f}")
        if best is not None:
            lines.append(f"  En iyi:  {best.position.symbol} {best.position.pnl:+.2f}")
        if worst is not None and worst is not best:
            lines.append(f"  En kötü: {worst.position.symbol} {worst.position.pnl:+.2f}")
    else:
        lines.append("Win rate (24h): — (hiç trade yok)")
    lines.append("")
    lines.append(f"Açık: {len(broker.open_positions)} pozisyon")
    lines.extend(open_lines)
    lines.append(f"Pending: {len(broker.pending_positions)}")
    lines.extend(pending_lines)
    return "\n".join(lines)


def format_position_closed(pos) -> str:  # type: Position
    side_label = "🟢 LONG" if pos.side.value == "bull" else "🔴 SHORT"
    pnl_emoji = "🟢" if pos.pnl >= 0 else "🔴"
    reason = pos.close_reason.value if pos.close_reason else "?"
    tr_time = to_tr(pos.closed_ts).strftime("%H:%M TR") if pos.closed_ts else "?"
    exit_price = pos.exit_price if pos.exit_price is not None else 0.0
    fill_entry = pos.fill_entry if pos.fill_entry is not None else 0.0
    return (
        f"📤 EXIT  {pnl_emoji} {pos.pnl:+.2f}\n"
        f"{pos.symbol}  {side_label}\n"
        f"Reason: {reason}\n"
        f"Exit:   {exit_price:.4f}\n"
        f"Entry:  {fill_entry:.4f}\n"
        f"Time:   {tr_time}"
    )
