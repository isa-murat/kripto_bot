"""Tests for Telegram message formatting (no network)."""

from __future__ import annotations

from datetime import datetime, timezone

from src.ict.structure import Trend
from src.notify.telegram import format_signal_message
from src.strategies.sweep_fvg import TradeSignal

UTC = timezone.utc


def _signal(**overrides) -> TradeSignal:
    base = dict(
        symbol="BTCUSDT",
        side=Trend.BULL,
        entry_price=95430.5,
        stop_loss=95280.0,
        take_profit=95740.0,
        rr=2.06,
        setup_name="sweep_fvg",
        bar_index=42,
        ts=datetime(2026, 5, 11, 7, 35, tzinfo=UTC),  # 10:35 TR
        meta={
            "sweep_pool_price": 95450.0,
            "sweep_bar_index": 39,
            "mss_event_type": "mss",
            "mss_bar_index": 40,
            "fvg_top": 95440.0,
            "fvg_bottom": 95420.0,
            "htf_trend": "bull",
            "htf_zone": "discount",
        },
    )
    base.update(overrides)
    return TradeSignal(**base)


def test_format_long_signal_contains_key_fields():
    msg = format_signal_message(_signal())
    assert "SWEEP_FVG SIGNAL" in msg
    assert "BTCUSDT" in msg
    assert "🟢 LONG" in msg
    assert "95430.5000" in msg            # entry
    assert "95280.0000" in msg            # SL
    assert "95740.0000" in msg            # TP
    assert "2.06" in msg                  # RR
    assert "10:35 TR" in msg              # 07:35 UTC → 10:35 TR


def test_format_short_signal_uses_red_marker():
    msg = format_signal_message(_signal(side=Trend.BEAR))
    assert "🔴 SHORT" in msg


def test_format_includes_meta_fields():
    msg = format_signal_message(_signal())
    assert "trend=bull" in msg
    assert "zone=discount" in msg
    assert "95420.0" in msg              # FVG bottom
    assert "mss" in msg                  # MSS event type


def test_format_handles_missing_meta_fields():
    msg = format_signal_message(_signal(meta={}))  # no metadata
    assert "?" in msg                    # placeholders for missing keys
    assert "BTCUSDT" in msg              # core fields still rendered
