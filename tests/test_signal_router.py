"""Unit tests for src.engine.signal_router."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.engine.signal_router import FilterReason, SignalRouter
from src.ict.structure import Trend
from src.strategies.sweep_fvg import TradeSignal

UTC = timezone.utc


def _signal(
    symbol: str = "BTCUSDT",
    bar_index: int = 100,
    side: Trend = Trend.BULL,
    ts: datetime | None = None,
    rr: float = 2.5,
    fvg_middle_index: int | None = None,
) -> TradeSignal:
    meta = {}
    if fvg_middle_index is not None:
        meta["fvg_middle_index"] = fvg_middle_index
    return TradeSignal(
        symbol=symbol, side=side,
        entry_price=100.0, stop_loss=99.0, take_profit=102.5, rr=rr,
        setup_name="sweep_fvg",
        bar_index=bar_index,
        ts=ts or datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
        meta=meta,
    )


# ============================== dispatch & dedup ============================ #


@pytest.mark.asyncio
async def test_first_signal_dispatched():
    router = SignalRouter(cooldown_minutes=30, max_concurrent=2)
    received: list[TradeSignal] = []

    async def handler(sig):
        received.append(sig)

    router.add_handler(handler)
    ok, reason = await router.submit(_signal())
    assert ok is True
    assert reason is None
    assert len(received) == 1


@pytest.mark.asyncio
async def test_dedup_blocks_same_symbol_and_bar_twice():
    router = SignalRouter(cooldown_minutes=0, max_concurrent=10)
    sig = _signal(bar_index=42)
    ok1, _ = await router.submit(sig)
    ok2, reason = await router.submit(sig)
    assert ok1 is True
    assert ok2 is False
    assert reason == FilterReason.DEDUP


@pytest.mark.asyncio
async def test_different_bar_index_passes_dedup():
    router = SignalRouter(cooldown_minutes=0, max_concurrent=10)
    sig1 = _signal(bar_index=10, ts=datetime(2026, 5, 11, 12, 0, tzinfo=UTC))
    sig2 = _signal(bar_index=11, ts=datetime(2026, 5, 11, 12, 5, tzinfo=UTC))
    ok1, _ = await router.submit(sig1)
    ok2, _ = await router.submit(sig2)
    assert ok1 and ok2


# ============================== cooldown =================================== #


@pytest.mark.asyncio
async def test_cooldown_blocks_within_window():
    router = SignalRouter(cooldown_minutes=30, max_concurrent=10)
    base = datetime(2026, 5, 11, 12, 0, tzinfo=UTC)
    ok1, _ = await router.submit(_signal(bar_index=1, ts=base))
    ok2, reason = await router.submit(_signal(
        bar_index=2, ts=base + timedelta(minutes=10),
    ))
    assert ok1 is True
    assert ok2 is False
    assert reason == FilterReason.COOLDOWN


@pytest.mark.asyncio
async def test_cooldown_passes_after_window():
    router = SignalRouter(cooldown_minutes=30, max_concurrent=10)
    base = datetime(2026, 5, 11, 12, 0, tzinfo=UTC)
    await router.submit(_signal(bar_index=1, ts=base))
    ok, _ = await router.submit(_signal(
        bar_index=2, ts=base + timedelta(minutes=31),
    ))
    assert ok is True


@pytest.mark.asyncio
async def test_cooldown_independent_per_symbol():
    """A cooldown on BTC must not block ETH signals."""
    router = SignalRouter(cooldown_minutes=30, max_concurrent=10)
    base = datetime(2026, 5, 11, 12, 0, tzinfo=UTC)
    await router.submit(_signal(symbol="BTCUSDT", bar_index=1, ts=base))
    ok, _ = await router.submit(_signal(
        symbol="ETHUSDT", bar_index=1,
        ts=base + timedelta(minutes=5),
    ))
    assert ok is True


# ============================== concurrency ================================= #


@pytest.mark.asyncio
async def test_max_concurrent_blocks_when_full():
    router = SignalRouter(cooldown_minutes=0, max_concurrent=2)
    router.set_open_positions(2)
    ok, reason = await router.submit(_signal())
    assert ok is False
    assert reason == FilterReason.MAX_CONCURRENT


@pytest.mark.asyncio
async def test_max_concurrent_passes_when_below_limit():
    router = SignalRouter(cooldown_minutes=0, max_concurrent=2)
    router.set_open_positions(1)
    ok, _ = await router.submit(_signal())
    assert ok is True


# ============================== provider semantics ========================== #


@pytest.mark.asyncio
async def test_provider_overrides_legacy_counter_so_state_cant_drift():
    """A live provider always wins over the cached `set_open_positions` value.
    Regression: closed positions used to leak into the count."""
    live_count = {"n": 0}
    router = SignalRouter(
        cooldown_minutes=0, max_concurrent=2,
        active_count_provider=lambda: live_count["n"],
    )
    router.set_open_positions(99)   # stale stuff; provider should ignore it
    ok, _ = await router.submit(_signal(bar_index=1))
    assert ok is True               # provider says 0, room for new
    live_count["n"] = 2
    ok2, reason = await router.submit(_signal(bar_index=2,
                                              ts=datetime(2026, 5, 11, 13, 0, tzinfo=UTC)))
    assert ok2 is False
    assert reason == FilterReason.MAX_CONCURRENT


@pytest.mark.asyncio
async def test_three_concurrent_then_one_closes_then_fourth_passes():
    """Acceptance: max_concurrent=3 → 4th rejected; once one closes, next passes."""
    live_count = {"n": 0}
    router = SignalRouter(
        cooldown_minutes=0, max_concurrent=3,
        active_count_provider=lambda: live_count["n"],
    )

    async def handler(sig):
        live_count["n"] += 1
    router.add_handler(handler)

    base = datetime(2026, 5, 11, 12, 0, tzinfo=UTC)
    for i in range(3):
        ok, _ = await router.submit(
            _signal(bar_index=i, ts=base + timedelta(minutes=i),
                    fvg_middle_index=10 + i)
        )
        assert ok is True
    # 4th rejected
    ok4, reason = await router.submit(
        _signal(bar_index=4, ts=base + timedelta(minutes=4), fvg_middle_index=14)
    )
    assert ok4 is False
    assert reason == FilterReason.MAX_CONCURRENT
    # One closes
    live_count["n"] = 2
    ok5, _ = await router.submit(
        _signal(bar_index=5, ts=base + timedelta(minutes=5), fvg_middle_index=15)
    )
    assert ok5 is True


# ============================== duplicate filter =========================== #


class _FakePos:
    def __init__(self, symbol, side, entry):
        self.symbol = symbol
        self.side = side
        self.requested_entry = entry


@pytest.mark.asyncio
async def test_duplicate_filter_rejects_same_symbol_side_entry():
    positions = [_FakePos("BTCUSDT", Trend.BULL, 100.0)]
    router = SignalRouter(
        cooldown_minutes=0, max_concurrent=10,
        active_positions_provider=lambda: positions,
    )
    # Within 0.05% tolerance (100.04 vs 100.0 → 0.04%) → DUPLICATE
    sig = TradeSignal(
        symbol="BTCUSDT", side=Trend.BULL,
        entry_price=100.04, stop_loss=99.04, take_profit=102.04, rr=2.5,
        setup_name="sweep_fvg", bar_index=200,
        ts=datetime(2026, 5, 11, 12, 0, tzinfo=UTC), meta={},
    )
    ok, reason = await router.submit(sig)
    assert ok is False
    assert reason == FilterReason.DUPLICATE


@pytest.mark.asyncio
async def test_duplicate_filter_allows_opposite_side():
    positions = [_FakePos("BTCUSDT", Trend.BULL, 100.0)]
    router = SignalRouter(
        cooldown_minutes=0, max_concurrent=10,
        active_positions_provider=lambda: positions,
    )
    sig = TradeSignal(
        symbol="BTCUSDT", side=Trend.BEAR,
        entry_price=100.0, stop_loss=101.0, take_profit=98.0, rr=2.0,
        setup_name="sweep_fvg", bar_index=200,
        ts=datetime(2026, 5, 11, 12, 0, tzinfo=UTC), meta={},
    )
    ok, _ = await router.submit(sig)
    assert ok is True


@pytest.mark.asyncio
async def test_duplicate_filter_allows_distant_entry():
    positions = [_FakePos("BTCUSDT", Trend.BULL, 100.0)]
    router = SignalRouter(
        cooldown_minutes=0, max_concurrent=10,
        active_positions_provider=lambda: positions,
    )
    # 1% away — outside the 0.05% tolerance
    sig = TradeSignal(
        symbol="BTCUSDT", side=Trend.BULL,
        entry_price=101.0, stop_loss=100.0, take_profit=103.5, rr=2.5,
        setup_name="sweep_fvg", bar_index=200,
        ts=datetime(2026, 5, 11, 12, 0, tzinfo=UTC), meta={},
    )
    ok, _ = await router.submit(sig)
    assert ok is True


# ============================== handlers =================================== #


@pytest.mark.asyncio
async def test_multiple_handlers_called_in_order():
    router = SignalRouter(cooldown_minutes=0, max_concurrent=10)
    order: list[str] = []

    async def h1(sig):
        order.append("h1")

    async def h2(sig):
        order.append("h2")

    router.add_handler(h1)
    router.add_handler(h2)
    await router.submit(_signal())
    assert order == ["h1", "h2"]


@pytest.mark.asyncio
async def test_handler_exception_does_not_block_subsequent_handlers():
    router = SignalRouter(cooldown_minutes=0, max_concurrent=10)
    called: list[str] = []

    async def bad(sig):
        raise RuntimeError("boom")

    async def good(sig):
        called.append("good")

    router.add_handler(bad)
    router.add_handler(good)
    ok, _ = await router.submit(_signal())
    assert ok is True
    assert called == ["good"]


@pytest.mark.asyncio
async def test_no_handlers_still_dispatched():
    router = SignalRouter(cooldown_minutes=0, max_concurrent=10)
    ok, _ = await router.submit(_signal())
    assert ok is True


# ============================== POI dedup ================================== #


@pytest.mark.asyncio
async def test_same_fvg_cannot_fire_twice():
    """Two signals from the same FVG (same fvg_middle_index) → second rejected."""
    router = SignalRouter(cooldown_minutes=0, max_concurrent=10)
    base = datetime(2026, 5, 11, 12, 0, tzinfo=UTC)
    sig1 = _signal(bar_index=10, ts=base, fvg_middle_index=42)
    # Different bar (passes (symbol,bar_index) dedup), past cooldown,
    # but SAME FVG → must reject.
    sig2 = _signal(bar_index=12, ts=base + timedelta(minutes=10), fvg_middle_index=42)
    ok1, _ = await router.submit(sig1)
    ok2, reason = await router.submit(sig2)
    assert ok1 is True
    assert ok2 is False
    assert reason == FilterReason.POI_CONSUMED


@pytest.mark.asyncio
async def test_different_fvgs_can_both_fire():
    router = SignalRouter(cooldown_minutes=0, max_concurrent=10)
    base = datetime(2026, 5, 11, 12, 0, tzinfo=UTC)
    sig1 = _signal(bar_index=10, ts=base, fvg_middle_index=42)
    sig2 = _signal(bar_index=11, ts=base + timedelta(minutes=5), fvg_middle_index=43)
    ok1, _ = await router.submit(sig1)
    ok2, _ = await router.submit(sig2)
    assert ok1 and ok2


@pytest.mark.asyncio
async def test_signal_without_fvg_index_not_dedup_by_poi():
    """Defensive: legacy signals without fvg_middle_index aren't blocked."""
    router = SignalRouter(cooldown_minutes=0, max_concurrent=10)
    base = datetime(2026, 5, 11, 12, 0, tzinfo=UTC)
    sig1 = _signal(bar_index=10, ts=base)             # no fvg_middle_index
    sig2 = _signal(bar_index=11, ts=base + timedelta(minutes=5))
    ok1, _ = await router.submit(sig1)
    ok2, _ = await router.submit(sig2)
    assert ok1 and ok2  # both pass; only generic dedup applies


# ============================== regime_check ================================ #


@pytest.mark.asyncio
async def test_regime_check_blocks_when_not_tradeable():
    """If regime_check returns (False, 'ranging'), signal is rejected with
    reason 'regime_ranging' and handlers are not called."""
    received: list[TradeSignal] = []

    async def handler(sig):
        received.append(sig)

    router = SignalRouter(
        cooldown_minutes=0, max_concurrent=10,
        regime_check=lambda s: (False, "ranging"),
    )
    router.add_handler(handler)
    ok, reason = await router.submit(_signal())
    assert ok is False
    assert reason == "regime_ranging"
    assert received == []


@pytest.mark.asyncio
async def test_regime_check_passes_when_tradeable():
    router = SignalRouter(
        cooldown_minutes=0, max_concurrent=10,
        regime_check=lambda s: (True, "bullish"),
    )
    ok, reason = await router.submit(_signal())
    assert ok is True
    assert reason is None


@pytest.mark.asyncio
async def test_regime_check_failure_does_not_block_signal():
    """If the closure raises, the gate is bypassed (fail-open) — better to take
    a signal than crash on a classifier glitch."""
    def boom(_):
        raise RuntimeError("classifier broke")

    router = SignalRouter(
        cooldown_minutes=0, max_concurrent=10,
        regime_check=boom,
    )
    ok, reason = await router.submit(_signal())
    assert ok is True
    assert reason is None
