"""Signal router: filters TradeSignals and dispatches to subscribed handlers.

Filters (applied in order):
    1. Dedup        — same (symbol, bar_index) cannot be emitted twice
    2. Cooldown     — symbol must have aged at least `cooldown_minutes`
                      since its last accepted signal
    3. Concurrency  — open position count must be below `max_concurrent`

A handler is `async (TradeSignal) -> None`. Multiple handlers run sequentially
inside `submit()`; one handler's exception is logged but does not block other
handlers. Typical handlers:

    notify.telegram_handler  — formats and sends to Telegram
    engine.paper_broker      — opens a virtual position (Faz 3)
    engine.position_mgr      — registers SL/TP for tracking
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Iterable

from src.strategies.sweep_fvg import TradeSignal
from src.utils.logging import logger

UTC = timezone.utc

SignalHandler = Callable[[TradeSignal], Awaitable[None]]
ActiveCountProvider = Callable[[], int]
ActivePositionsProvider = Callable[[], Iterable[Any]]   # any iterable of position-likes
# Regime gate: (signal) -> (tradeable, regime_label). When tradeable=False the
# signal is rejected with reason="regime_<label>" (e.g. "regime_ranging").
RegimeCheck = Callable[[TradeSignal], tuple[bool, str]]


class FilterReason:
    DEDUP = "dedup"                  # exact (symbol, bar_index) replay
    POI_CONSUMED = "poi_consumed"    # FVG/POI already produced a signal
    DUPLICATE = "duplicate"          # same symbol+side+entry as live position
    COOLDOWN = "cooldown"
    MAX_CONCURRENT = "max_concurrent"
    REGIME = "regime"                # HTF regime gate (e.g. ranging)


class SignalRouter:
    def __init__(
        self,
        cooldown_minutes: int = 30,
        max_concurrent: int = 3,
        duplicate_entry_tolerance_pct: float = 0.0005,
        active_count_provider: ActiveCountProvider | None = None,
        active_positions_provider: ActivePositionsProvider | None = None,
        regime_check: RegimeCheck | None = None,
    ):
        """SignalRouter applies cooldown / dedup / duplicate / concurrency
        filters before fanning a signal out to handlers.

        State sources
        -------------
        - active_count_provider:     callable returning current open+pending
                                      position count. Preferred over the
                                      `set_open_positions` setter because it
                                      can never drift (it queries on demand).
        - active_positions_provider: callable returning iterable of open+pending
                                      positions for duplicate-entry detection.
        - regime_check:              optional gate run before cooldown/concurrency.
                                      Returns (tradeable, label); rejection
                                      reason is "regime_<label>" so log/JSON
                                      consumers can break it down.
        active_*_provider are optional; without them, callers must keep
        `set_open_positions` up to date and duplicate-entry filtering is skipped.
        """
        self._cooldown = timedelta(minutes=cooldown_minutes)
        self._max_concurrent = max_concurrent
        self._dup_tol_pct = duplicate_entry_tolerance_pct
        self._open_positions: int = 0      # legacy fallback, used if no provider
        self._active_count_provider = active_count_provider
        self._active_positions_provider = active_positions_provider
        self._regime_check = regime_check
        self._last_signal_ts: dict[str, datetime] = {}
        self._dispatched: set[tuple[str, int]] = set()
        # Track POIs (FVG/OB) that already triggered a signal. Same FVG must
        # never produce a second signal — even on a later bar — because the
        # setup it represents has already been actioned. Stops the "cluster
        # spam" where one FVG fires on 5 consecutive 5m bars.
        # Key: (symbol, fvg_middle_index)
        self._consumed_pois: set[tuple[str, int]] = set()
        self._handlers: list[SignalHandler] = []

    # --- handler registration -------------------------------------------- #

    def add_handler(self, handler: SignalHandler) -> None:
        self._handlers.append(handler)

    # --- external state writes ------------------------------------------- #

    def set_open_positions(self, count: int) -> None:
        """Updated by the position manager whenever an open position
        opens/closes. Used solely for the concurrency filter."""
        self._open_positions = max(0, int(count))

    # --- main entry ------------------------------------------------------ #

    async def submit(self, signal: TradeSignal) -> tuple[bool, str | None]:
        """Apply filters; if accepted, run all handlers.

        Returns `(dispatched, reject_reason_or_None)`.
        """
        reason = self._reject_reason(signal)
        if reason is not None:
            logger.info(
                "Signal filtered | {} | reason={} | bar={}",
                signal.symbol, reason, signal.bar_index,
            )
            return (False, reason)

        # Mark accepted BEFORE dispatch — handler exceptions don't replay
        self._dispatched.add((signal.symbol, signal.bar_index))
        self._last_signal_ts[signal.symbol] = _to_utc(signal.ts)
        poi_key = _poi_key(signal)
        if poi_key is not None:
            self._consumed_pois.add(poi_key)

        logger.info(
            "Signal dispatched | {} {} | entry={} sl={} tp={} rr={:.2f}",
            signal.symbol, signal.side.value,
            signal.entry_price, signal.stop_loss, signal.take_profit, signal.rr,
        )

        for handler in self._handlers:
            try:
                await handler(signal)
            except Exception as e:
                logger.exception("Signal handler error: {}", e)

        return (True, None)

    # --- internals ------------------------------------------------------- #

    def _reject_reason(self, signal: TradeSignal) -> str | None:
        if (signal.symbol, signal.bar_index) in self._dispatched:
            return FilterReason.DEDUP
        poi_key = _poi_key(signal)
        if poi_key is not None and poi_key in self._consumed_pois:
            return FilterReason.POI_CONSUMED
        if self._active_positions_provider is not None and self._is_duplicate(signal):
            return FilterReason.DUPLICATE
        # Regime gate runs before cooldown/concurrency so the dispatched-set
        # is not "consumed" by a signal that the gate would have killed anyway.
        if self._regime_check is not None:
            try:
                tradeable, label = self._regime_check(signal)
            except Exception as e:
                logger.warning("regime_check failed ({}): allowing signal", e)
            else:
                if not tradeable:
                    return f"{FilterReason.REGIME}_{label}"
        last = self._last_signal_ts.get(signal.symbol)
        if last is not None and (_to_utc(signal.ts) - last) < self._cooldown:
            return FilterReason.COOLDOWN
        if self._current_active_count() >= self._max_concurrent:
            return FilterReason.MAX_CONCURRENT
        return None

    def _current_active_count(self) -> int:
        """Live count if a provider is configured; else fall back to the
        legacy `set_open_positions`-driven counter."""
        if self._active_count_provider is not None:
            try:
                return int(self._active_count_provider())
            except Exception as e:
                logger.warning("active_count_provider failed: {} (using fallback)", e)
        return self._open_positions

    def _is_duplicate(self, signal: TradeSignal) -> bool:
        """A new signal is a duplicate when an existing PENDING/OPEN position
        has the same symbol+side AND entry within `dup_tol_pct` of the new one.
        Independent of `MAX_CONCURRENT` — duplicate is the same SETUP twice;
        max_concurrent is the global open-position cap."""
        positions = self._active_positions_provider() if self._active_positions_provider else []
        tol = abs(signal.entry_price) * self._dup_tol_pct
        for pos in positions:
            if getattr(pos, "symbol", None) != signal.symbol:
                continue
            pos_side = getattr(pos, "side", None)
            if pos_side != signal.side:
                continue
            pos_entry = getattr(pos, "requested_entry", None) or getattr(pos, "entry_price", None)
            if pos_entry is None:
                continue
            if abs(float(pos_entry) - signal.entry_price) <= tol:
                return True
        return False


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _poi_key(signal: TradeSignal) -> tuple[str, int] | None:
    """(symbol, fvg_middle_index) — uniquely identifies the FVG that fired the
    setup. None if meta lacks the index (defensive: legacy signals)."""
    fvg_idx = (signal.meta or {}).get("fvg_middle_index")
    if fvg_idx is None or not isinstance(fvg_idx, int):
        return None
    return (signal.symbol, fvg_idx)
