"""Position manager: drives PaperBroker on every closed bar.

Per bar:
    1. Try to fill any PENDING positions whose entry is inside the bar's range.
    2. For each OPEN position on this symbol, check SL/TP against bar high/low.
       If both are hit on the same bar (an inside-bar gap), assume SL hit first
       (conservative — this matches what most paper-trade frameworks do).
"""

from __future__ import annotations

from datetime import datetime
from typing import Awaitable, Callable

from src.engine.paper_broker import (
    CloseReason,
    PaperBroker,
    Position,
    PositionStatus,
)
from src.ict.structure import Trend
from src.utils.logging import logger

# Optional async hook fired when a position transitions OPEN/CLOSED — useful
# for Telegram notifications and SignalRouter.set_open_positions sync.
PositionEvent = Callable[[Position], Awaitable[None]]


class PositionManager:
    def __init__(
        self,
        broker: PaperBroker,
        on_filled: PositionEvent | None = None,
        on_closed: PositionEvent | None = None,
    ):
        self._broker = broker
        self._on_filled = on_filled
        self._on_closed = on_closed

    @property
    def broker(self) -> PaperBroker:
        return self._broker

    async def on_bar(
        self,
        symbol: str,
        bar_high: float,
        bar_low: float,
        bar_ts: datetime,
    ) -> None:
        """Process a closed bar: expire/invalidate pendings, fill new pendings,
        then check SL/TP on opens."""
        # 0a) TTL: stale pendings (no fill within window)
        self._broker.cancel_expired_pending(bar_ts)
        # 0b) Price-based invalidation: SL hit before fill
        self._broker.cancel_invalidated_pending(symbol, bar_high, bar_low, bar_ts)

        # 1) Fill pendings
        filled = self._broker.try_fill_pending(symbol, bar_high, bar_low, bar_ts)
        for pos in filled:
            if self._on_filled is not None:
                try:
                    await self._on_filled(pos)
                except Exception as e:
                    logger.exception("on_filled handler error: {}", e)

        # 2) SL/TP checks on opens
        for pos in self._broker.open_positions:
            if pos.symbol != symbol:
                continue
            close_at, reason = _resolve_close(pos, bar_high, bar_low)
            if reason is None:
                continue
            trade = self._broker.close_position(pos, close_at, reason, bar_ts)
            if self._on_closed is not None:
                try:
                    await self._on_closed(trade.position)
                except Exception as e:
                    logger.exception("on_closed handler error: {}", e)


def _resolve_close(
    pos: Position,
    bar_high: float,
    bar_low: float,
) -> tuple[float, CloseReason | None]:
    """Decide if a bar closes the position and at what price.

    Conservative tie-break: if both SL and TP are hit on the same bar, assume
    SL fills first.
    """
    if pos.status != PositionStatus.OPEN:
        return (0.0, None)

    if pos.side == Trend.BULL:
        sl_hit = bar_low <= pos.stop_loss
        tp_hit = bar_high >= pos.take_profit
        if sl_hit and tp_hit:
            return (pos.stop_loss, CloseReason.SL)
        if sl_hit:
            return (pos.stop_loss, CloseReason.SL)
        if tp_hit:
            return (pos.take_profit, CloseReason.TP)
    else:  # BEAR / SHORT
        sl_hit = bar_high >= pos.stop_loss
        tp_hit = bar_low <= pos.take_profit
        if sl_hit and tp_hit:
            return (pos.stop_loss, CloseReason.SL)
        if sl_hit:
            return (pos.stop_loss, CloseReason.SL)
        if tp_hit:
            return (pos.take_profit, CloseReason.TP)
    return (0.0, None)
