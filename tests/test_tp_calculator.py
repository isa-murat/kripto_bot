"""Tests for compute_tp — TP pool selection with min_rr/max_rr band."""

from __future__ import annotations

from src.ict.structure import Trend
from src.strategies.sweep_fvg import compute_tp


# --------- bullish: entry=100, sl=95 → risk=5; min_rr=2 → 110; max_rr=4 → 120 - #


def test_bullish_picks_nearest_pool_within_band():
    # Pools at 1R(105), 2R(110), 3R(115), 5R(125): 2R is the nearest valid.
    tp = compute_tp(
        entry=100, sl=95, side=Trend.BULL,
        pool_prices=[105, 110, 115, 125],
        min_rr=2.0, max_rr=4.0,
    )
    assert tp == 110


def test_bullish_rejects_when_only_below_min_rr():
    # Only a 1.5R pool exists → below min_rr → None
    tp = compute_tp(
        entry=100, sl=95, side=Trend.BULL,
        pool_prices=[107.5],
        min_rr=2.0, max_rr=4.0,
    )
    assert tp is None


def test_bullish_rejects_when_only_above_max_rr():
    # Only a 5R pool exists → beyond max_rr → None
    tp = compute_tp(
        entry=100, sl=95, side=Trend.BULL,
        pool_prices=[125],
        min_rr=2.0, max_rr=4.0,
    )
    assert tp is None


def test_bullish_pool_exactly_at_min_rr_is_accepted():
    # 110 == entry + 2R; lower bound inclusive
    tp = compute_tp(
        entry=100, sl=95, side=Trend.BULL,
        pool_prices=[110],
        min_rr=2.0, max_rr=4.0,
    )
    assert tp == 110


def test_bullish_pool_exactly_at_max_rr_is_accepted():
    # 120 == entry + 4R; upper bound inclusive
    tp = compute_tp(
        entry=100, sl=95, side=Trend.BULL,
        pool_prices=[120],
        min_rr=2.0, max_rr=4.0,
    )
    assert tp == 120


def test_bullish_below_entry_ignored():
    # A pool below entry is not a valid TP for a long; must be ignored.
    tp = compute_tp(
        entry=100, sl=95, side=Trend.BULL,
        pool_prices=[90, 110],
        min_rr=2.0, max_rr=4.0,
    )
    assert tp == 110


# --------- bearish: entry=100, sl=105 → risk=5; min_rr=2 → 90; max_rr=4 → 80 - #


def test_bearish_picks_nearest_pool_within_band():
    # Pools at 1R(95), 2R(90), 3R(85), 5R(75): 2R nearest valid below entry.
    tp = compute_tp(
        entry=100, sl=105, side=Trend.BEAR,
        pool_prices=[95, 90, 85, 75],
        min_rr=2.0, max_rr=4.0,
    )
    assert tp == 90


def test_bearish_rejects_when_only_below_min_rr():
    tp = compute_tp(
        entry=100, sl=105, side=Trend.BEAR,
        pool_prices=[92.5],
        min_rr=2.0, max_rr=4.0,
    )
    assert tp is None


def test_bearish_rejects_when_only_above_max_rr():
    tp = compute_tp(
        entry=100, sl=105, side=Trend.BEAR,
        pool_prices=[75],
        min_rr=2.0, max_rr=4.0,
    )
    assert tp is None


def test_bearish_above_entry_ignored():
    tp = compute_tp(
        entry=100, sl=105, side=Trend.BEAR,
        pool_prices=[110, 90],
        min_rr=2.0, max_rr=4.0,
    )
    assert tp == 90


# --------- edge cases ---------------------------------------------------------- #


def test_empty_pool_list_returns_none():
    assert compute_tp(
        entry=100, sl=95, side=Trend.BULL, pool_prices=[],
        min_rr=2.0, max_rr=4.0,
    ) is None


def test_zero_risk_returns_none():
    assert compute_tp(
        entry=100, sl=100, side=Trend.BULL, pool_prices=[110],
        min_rr=2.0, max_rr=4.0,
    ) is None


def test_neutral_side_returns_none():
    assert compute_tp(
        entry=100, sl=95, side=Trend.NEUTRAL, pool_prices=[110],
        min_rr=2.0, max_rr=4.0,
    ) is None
