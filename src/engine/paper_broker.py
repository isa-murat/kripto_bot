"""Paper broker — virtual portfolio with realistic fee + slippage simulation.

Fill model
----------
Signals come in as **limit** orders at the FVG midpoint. We don't simulate
the full order book; instead, when `try_fill_pending(bar)` is called, the
order fills if the bar's range overlaps the entry price (long: low <= entry,
short: high >= entry). Slippage of `slippage_ticks * tick_size` (config) is
applied against the trader on fill.

A position carries entry/SL/TP captured from the originating signal.
PositionManager.on_bar() drives SL/TP checks against subsequent bars.

Persistence
-----------
State is snapshotted to a JSON file under `data/paper/state.json` after every
mutation. Restart loads the snapshot so equity, open positions and trade log
survive process restarts.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from src.config import REPO_ROOT, get_env, get_settings
from src.ict.structure import Trend
from src.strategies.sweep_fvg import TradeSignal
from src.utils.logging import logger

UTC = timezone.utc


class PositionStatus(str, Enum):
    PENDING = "pending"   # limit order placed, not yet filled
    OPEN = "open"         # filled, SL/TP active
    CLOSED = "closed"     # SL or TP hit, or manually closed


class CloseReason(str, Enum):
    TP = "take_profit"
    SL = "stop_loss"
    MANUAL = "manual"
    EXPIRED = "expired"          # PENDING TTL'i geçti, fill olmadı
    INVALIDATED = "invalidated"  # PENDING fill olmadan setup'ın SL seviyesi vuruldu


@dataclass
class Position:
    id: str                       # stable id: "{symbol}_{bar_index}_{ts_epoch}"
    symbol: str
    side: Trend                   # BULL = LONG, BEAR = SHORT
    requested_entry: float        # the original signal entry (FVG mid)
    fill_entry: float | None      # actual fill price after slippage; None until filled
    stop_loss: float
    take_profit: float
    size: float                   # contract size in base asset (computed from risk %)
    risk_amount: float            # equity-denominated risk at open
    entry_fee: float              # fee paid on entry fill (maker — limit order at FVG mid)
    status: PositionStatus
    opened_ts: datetime           # signal time
    filled_ts: datetime | None    # fill time (None until filled)
    closed_ts: datetime | None
    close_reason: CloseReason | None
    exit_price: float | None
    exit_fee: float               # fee paid on exit (TP → maker, SL/EXPIRED → taker)
    pnl: float                    # net P&L in quote asset (after fees)
    setup_name: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClosedTrade:
    """Snapshot of a closed Position, kept in trade log for reporting."""
    position: Position
    pnl_pct_of_equity: float


@dataclass
class PaperState:
    equity: float
    open_positions: list[Position] = field(default_factory=list)
    pending_positions: list[Position] = field(default_factory=list)
    closed_trades: list[ClosedTrade] = field(default_factory=list)


class PaperBroker:
    def __init__(
        self,
        initial_equity: float | None = None,
        risk_per_trade_pct: float | None = None,
        fee_taker: float | None = None,
        fee_maker: float | None = None,
        slippage_ticks: int | None = None,
        snapshot_path: Path | None = None,
        persist: bool = True,
    ):
        env = get_env()
        settings = get_settings()
        self._initial_equity = initial_equity if initial_equity is not None else env.paper_initial_equity
        self._risk_per_trade_pct = risk_per_trade_pct if risk_per_trade_pct is not None else env.paper_risk_per_trade
        # Fee model: limit fills (entry at FVG mid, TP at target) pay maker;
        # market exits (SL trigger, mark-to-end EXPIRED) pay taker.
        self._fee_taker = fee_taker if fee_taker is not None else settings.paper.fee_taker
        self._fee_maker = fee_maker if fee_maker is not None else settings.paper.fee_maker
        self._slippage_ticks = slippage_ticks if slippage_ticks is not None else settings.paper.slippage_ticks
        # Max effective leverage — caps `size` so that `notional = size × entry`
        # never exceeds `equity × max_leverage`. Crucial for ICT scalping where
        # tight SLs would otherwise compute insane notionals.
        self._max_leverage = 10.0
        # Pending TTL: a PENDING position whose entry hasn't filled within
        # this duration is cancelled. Prevents stale pendings from hogging
        # the SignalRouter's max_concurrent slot. Computed from
        # `paper.pending_ttl_bars × entry_timeframe` (default 24 × 5m = 120min).
        from src.utils.time import timeframe_to_seconds
        ttl_bars = settings.paper.pending_ttl_bars
        tf_secs = timeframe_to_seconds(settings.timeframes.entry)
        self._pending_ttl = timedelta(seconds=ttl_bars * tf_secs)
        self._persist = persist
        self._snapshot_path = snapshot_path or (REPO_ROOT / "data" / "paper" / "state.json")
        if self._persist:
            self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)

        self._state = self._load_state() if self._persist else PaperState(equity=self._initial_equity)

    # ----- public API ---------------------------------------------------- #

    @property
    def equity(self) -> float:
        return self._state.equity

    @property
    def open_positions(self) -> list[Position]:
        return list(self._state.open_positions)

    @property
    def pending_positions(self) -> list[Position]:
        return list(self._state.pending_positions)

    @property
    def closed_trades(self) -> list[ClosedTrade]:
        return list(self._state.closed_trades)

    def total_active(self) -> int:
        return len(self._state.open_positions) + len(self._state.pending_positions)

    def open_from_signal(self, signal: TradeSignal) -> Position | None:
        """Create a PENDING position from a TradeSignal. Returns None on bad sizing.

        Note: duplicate detection (same symbol+side+entry) lives in
        SignalRouter. This method assumes the router already approved.
        """
        risk_amount = self._state.equity * self._risk_per_trade_pct
        per_unit_risk = abs(signal.entry_price - signal.stop_loss)
        if per_unit_risk <= 0:
            logger.warning("open_from_signal: zero per-unit risk, dropping {}", signal.symbol)
            return None
        size = risk_amount / per_unit_risk
        if size <= 0:
            return None

        # Leverage cap: notional must not exceed equity × max_leverage.
        # When tight SLs would imply huge notionals, we shrink the position
        # (the realised risk becomes smaller than the risk_amount target).
        max_notional = self._state.equity * self._max_leverage
        notional = size * signal.entry_price
        if notional > max_notional:
            size = max_notional / signal.entry_price
            risk_amount = size * per_unit_risk     # actual risk after capping
            logger.debug(
                "Size capped by leverage | {} | {:.4f} (notional {:.0f} > cap {:.0f})",
                signal.symbol, size, notional, max_notional,
            )

        pos = Position(
            id=f"{signal.symbol}_{signal.bar_index}_{int(signal.ts.timestamp())}",
            symbol=signal.symbol,
            side=signal.side,
            requested_entry=signal.entry_price,
            fill_entry=None,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            size=size,
            risk_amount=risk_amount,
            entry_fee=0.0,
            status=PositionStatus.PENDING,
            opened_ts=_to_utc(signal.ts),
            filled_ts=None,
            closed_ts=None,
            close_reason=None,
            exit_price=None,
            exit_fee=0.0,
            pnl=0.0,
            setup_name=signal.setup_name,
            meta={
                "signal_rr": signal.rr,
                **(signal.meta or {}),
            },
        )
        self._state.pending_positions.append(pos)
        logger.info(
            "Position PENDING | id={} {} {} | entry={} sl={} tp={} size={:.6f} risk={:.2f}",
            pos.id, pos.symbol, pos.side.value,
            pos.requested_entry, pos.stop_loss, pos.take_profit, pos.size, pos.risk_amount,
        )
        self._save_state()
        return pos

    def cancel_invalidated_pending(
        self,
        symbol: str,
        bar_high: float,
        bar_low: float,
        bar_ts: datetime,
    ) -> list[Position]:
        """Cancel PENDING positions whose SL would have been hit before fill.

        For a bullish PENDING (entry above current price, SL below): if the
        bar prints below SL but doesn't reach the entry, the setup is broken
        before we can take it — `INVALIDATED`. Symmetric for bearish.

        Fill takes precedence: if the same bar covers both SL and entry,
        `try_fill_pending` runs first and the position is filled (then the
        SL check happens against the OPEN position, not invalidation).
        """
        invalidated: list[Position] = []
        remaining: list[Position] = []
        cur = _to_utc(bar_ts)
        for pos in self._state.pending_positions:
            if pos.symbol != symbol:
                remaining.append(pos)
                continue

            # If this bar would also fill the position, defer to try_fill_pending
            entry_in_bar = bar_low <= pos.requested_entry <= bar_high
            if entry_in_bar:
                remaining.append(pos)
                continue

            invalid = False
            if pos.side == Trend.BULL:
                if bar_low <= pos.stop_loss:
                    invalid = True
            else:  # BEAR
                if bar_high >= pos.stop_loss:
                    invalid = True

            if invalid:
                pos.status = PositionStatus.CLOSED
                pos.close_reason = CloseReason.INVALIDATED
                pos.closed_ts = cur
                invalidated.append(pos)
                logger.info(
                    "Position INVALIDATED (SL hit pre-fill) | id={} side={} sl={:.4f}",
                    pos.id, pos.side.value, pos.stop_loss,
                )
            else:
                remaining.append(pos)

        if invalidated:
            self._state.pending_positions = remaining
            self._save_state()
        return invalidated

    def cancel_expired_pending(self, current_ts: datetime) -> list[Position]:
        """Cancel any PENDING positions older than `_pending_ttl`.

        Returns the cancelled positions (now CLOSED with reason=EXPIRED).
        Frees up SignalRouter's max_concurrent slot for newer setups.
        """
        cancelled: list[Position] = []
        remaining: list[Position] = []
        cur = _to_utc(current_ts)
        for pos in self._state.pending_positions:
            opened = _to_utc(pos.opened_ts) if pos.opened_ts else None
            if opened is not None and (cur - opened) > self._pending_ttl:
                pos.status = PositionStatus.CLOSED
                pos.close_reason = CloseReason.EXPIRED
                pos.closed_ts = cur
                cancelled.append(pos)
                logger.info("Position EXPIRED (pending TTL) | id={}", pos.id)
            else:
                remaining.append(pos)
        if cancelled:
            self._state.pending_positions = remaining
            self._save_state()
        return cancelled

    def try_fill_pending(self, symbol: str, bar_high: float, bar_low: float, bar_ts: datetime) -> list[Position]:
        """Fill any PENDING positions for this symbol whose entry price was hit by `bar`."""
        filled: list[Position] = []
        remaining: list[Position] = []
        for pos in self._state.pending_positions:
            if pos.symbol != symbol:
                remaining.append(pos)
                continue
            entry = pos.requested_entry
            hit = (bar_low <= entry <= bar_high)
            if not hit:
                remaining.append(pos)
                continue
            # Apply slippage against the trader (worse fill)
            slip = self._slippage_ticks * _tick_size(entry)
            fill_price = entry + slip if pos.side == Trend.BULL else entry - slip
            # Entries are limit orders parked at the FVG midpoint → maker fee.
            fee = abs(fill_price * pos.size) * self._fee_maker
            pos.fill_entry = fill_price
            pos.entry_fee = fee
            pos.filled_ts = _to_utc(bar_ts)
            pos.status = PositionStatus.OPEN
            self._state.equity -= fee  # entry fee taken immediately
            self._state.open_positions.append(pos)
            filled.append(pos)
            logger.info(
                "Position FILLED | id={} fill={:.4f} fee={:.4f} equity={:.2f}",
                pos.id, fill_price, fee, self._state.equity,
            )
        self._state.pending_positions = remaining
        if filled:
            self._save_state()
        return filled

    def close_position(
        self,
        pos: Position,
        exit_price: float,
        reason: CloseReason,
        bar_ts: datetime,
    ) -> ClosedTrade:
        """Close `pos` at `exit_price`, settle PnL, move to closed_trades."""
        # Apply slippage on exit
        slip = self._slippage_ticks * _tick_size(exit_price)
        fill_price = exit_price - slip if pos.side == Trend.BULL else exit_price + slip
        # TP fills are resting limit orders at the target → maker. SL exits are
        # stop-market (triggered, then market) → taker. EXPIRED mark-to-end and
        # MANUAL closes are market exits → taker.
        exit_rate = self._fee_maker if reason == CloseReason.TP else self._fee_taker
        fee = abs(fill_price * pos.size) * exit_rate

        if pos.fill_entry is None:
            raise RuntimeError(f"close_position called on un-filled {pos.id}")
        gross = (fill_price - pos.fill_entry) * pos.size
        if pos.side == Trend.BEAR:
            gross = -gross
        # `pos.entry_fee` was already deducted from equity at fill time. Folding
        # it into `pnl` keeps PnL consistent with equity changes (otherwise
        # winning trades look bigger than they really are).
        net = gross - fee - pos.entry_fee

        pos.exit_price = fill_price
        pos.exit_fee = fee
        pos.pnl = net
        pos.closed_ts = _to_utc(bar_ts)
        pos.close_reason = reason
        pos.status = PositionStatus.CLOSED

        self._state.equity += pos.size * (fill_price - pos.fill_entry) * (1 if pos.side == Trend.BULL else -1)
        self._state.equity -= fee

        # Remove from open list
        self._state.open_positions = [p for p in self._state.open_positions if p.id != pos.id]

        trade = ClosedTrade(
            position=pos,
            pnl_pct_of_equity=net / max(self._state.equity, 1e-9),
        )
        self._state.closed_trades.append(trade)
        logger.info(
            "Position CLOSED | id={} reason={} exit={:.4f} pnl={:.2f} equity={:.2f}",
            pos.id, reason.value, fill_price, net, self._state.equity,
        )
        self._save_state()
        return trade

    # ----- persistence --------------------------------------------------- #

    def _load_state(self) -> PaperState:
        if not self._snapshot_path.exists():
            logger.info("Paper state: fresh (initial equity={})", self._initial_equity)
            return PaperState(equity=self._initial_equity)
        try:
            data = json.loads(self._snapshot_path.read_text(encoding="utf-8"))
            state = PaperState(
                equity=float(data["equity"]),
                open_positions=[_position_from_dict(d) for d in data.get("open_positions", [])],
                pending_positions=[_position_from_dict(d) for d in data.get("pending_positions", [])],
                closed_trades=[
                    ClosedTrade(
                        position=_position_from_dict(t["position"]),
                        pnl_pct_of_equity=float(t["pnl_pct_of_equity"]),
                    )
                    for t in data.get("closed_trades", [])
                ],
            )
            logger.info(
                "Paper state restored | equity={:.2f} | open={} pending={} closed={}",
                state.equity, len(state.open_positions),
                len(state.pending_positions), len(state.closed_trades),
            )
            return state
        except Exception as e:
            logger.exception("Paper state load failed, starting fresh: {}", e)
            return PaperState(equity=self._initial_equity)

    def _save_state(self) -> None:
        if not self._persist:
            return
        try:
            payload = {
                "equity": self._state.equity,
                "open_positions": [_position_to_dict(p) for p in self._state.open_positions],
                "pending_positions": [_position_to_dict(p) for p in self._state.pending_positions],
                "closed_trades": [
                    {
                        "position": _position_to_dict(t.position),
                        "pnl_pct_of_equity": t.pnl_pct_of_equity,
                    }
                    for t in self._state.closed_trades
                ],
                "saved_at": datetime.now(UTC).isoformat(),
            }
            tmp = self._snapshot_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
            tmp.replace(self._snapshot_path)
        except Exception as e:
            logger.exception("Paper state save failed: {}", e)


# --------------------------------- helpers ---------------------------------- #


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _tick_size(price: float) -> float:
    """Crude tick-size approximation: 1e-6 of price magnitude.
    Refined per-symbol in Faz 5 from exchange info."""
    if price <= 0:
        return 0.0
    return max(price * 1e-5, 1e-8)


def _position_to_dict(p: Position) -> dict[str, Any]:
    d = asdict(p)
    d["side"] = p.side.value
    d["status"] = p.status.value
    d["close_reason"] = p.close_reason.value if p.close_reason else None
    d["opened_ts"] = p.opened_ts.isoformat() if p.opened_ts else None
    d["filled_ts"] = p.filled_ts.isoformat() if p.filled_ts else None
    d["closed_ts"] = p.closed_ts.isoformat() if p.closed_ts else None
    return d


def _position_from_dict(d: dict[str, Any]) -> Position:
    return Position(
        id=d["id"],
        symbol=d["symbol"],
        side=Trend(d["side"]),
        requested_entry=float(d["requested_entry"]),
        fill_entry=float(d["fill_entry"]) if d.get("fill_entry") is not None else None,
        stop_loss=float(d["stop_loss"]),
        take_profit=float(d["take_profit"]),
        size=float(d["size"]),
        risk_amount=float(d["risk_amount"]),
        # Snapshot key rename: pre-2026-05-11 snapshots used `fee_paid`. Accept
        # either spelling so existing state.json files load cleanly.
        entry_fee=float(d.get("entry_fee", d.get("fee_paid", 0.0))),
        status=PositionStatus(d["status"]),
        opened_ts=datetime.fromisoformat(d["opened_ts"]),
        filled_ts=datetime.fromisoformat(d["filled_ts"]) if d.get("filled_ts") else None,
        closed_ts=datetime.fromisoformat(d["closed_ts"]) if d.get("closed_ts") else None,
        close_reason=CloseReason(d["close_reason"]) if d.get("close_reason") else None,
        exit_price=float(d["exit_price"]) if d.get("exit_price") is not None else None,
        exit_fee=float(d.get("exit_fee", 0.0)),
        pnl=float(d.get("pnl", 0.0)),
        setup_name=d.get("setup_name", "unknown"),
        meta=dict(d.get("meta", {})),
    )
