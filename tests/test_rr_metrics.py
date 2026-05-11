"""Tests for src/utils/rr_metrics.py"""

from __future__ import annotations

import pytest

from src.utils.rr_metrics import (
    calculate_expectancy,
    calculate_net_rr,
    format_expectancy,
)


# ----------------------------- calculate_net_rr ----------------------------- #


def test_net_rr_long_basic_gross_2_0():
    # Entry 100, SL 95, TP 110 → gross RR = 10/5 = 2.0
    r = calculate_net_rr(entry=100.0, sl=95.0, tp=110.0, size=1.0)
    assert r["gross_rr"] == pytest.approx(2.0)


def test_net_rr_long_net_less_than_gross_after_fees():
    r = calculate_net_rr(entry=100.0, sl=95.0, tp=110.0, size=1.0,
                         maker_rate=0.0002, taker_rate=0.0004)
    assert r["net_rr"] < r["gross_rr"]
    # Fees positive
    assert r["entry_fee"] > 0
    assert r["tp_fee"] > 0
    assert r["sl_fee"] > 0
    # Total fees at TP exit = entry + TP (both maker)
    assert r["total_fees_at_tp"] == pytest.approx(r["entry_fee"] + r["tp_fee"])


def test_net_rr_entry_uses_maker_rate():
    """Entry fee = size * entry * maker_rate."""
    r = calculate_net_rr(entry=100.0, sl=95.0, tp=110.0, size=2.0,
                         maker_rate=0.0002, taker_rate=0.0004)
    assert r["entry_fee"] == pytest.approx(100.0 * 2.0 * 0.0002)


def test_net_rr_tp_exit_uses_maker_rate():
    """TP exit fee = size * tp * maker_rate."""
    r = calculate_net_rr(entry=100.0, sl=95.0, tp=110.0, size=2.0,
                         maker_rate=0.0002, taker_rate=0.0004)
    assert r["tp_fee"] == pytest.approx(110.0 * 2.0 * 0.0002)


def test_net_rr_sl_exit_uses_taker_rate():
    """SL exit fee = size * sl * taker_rate (NOT maker)."""
    r = calculate_net_rr(entry=100.0, sl=95.0, tp=110.0, size=2.0,
                         maker_rate=0.0002, taker_rate=0.0004)
    assert r["sl_fee"] == pytest.approx(95.0 * 2.0 * 0.0004)


def test_net_rr_short_side_inferred_from_tp_below_entry():
    # Entry 100, SL 105, TP 90 → bearish; gross RR = 10/5 = 2.0
    r = calculate_net_rr(entry=100.0, sl=105.0, tp=90.0, size=1.0)
    assert r["gross_rr"] == pytest.approx(2.0)
    assert r["net_profit_at_tp"] > 0
    assert r["net_loss_at_sl"] > 0


def test_net_rr_size_zero_returns_zeros_no_zerodivision():
    r = calculate_net_rr(entry=100.0, sl=95.0, tp=110.0, size=0.0)
    assert r["gross_rr"] == 0.0
    assert r["net_rr"] == 0.0
    assert r["total_fees_at_tp"] == 0.0


def test_net_rr_negative_size_returns_zeros():
    r = calculate_net_rr(entry=100.0, sl=95.0, tp=110.0, size=-1.0)
    assert r["gross_rr"] == 0.0


def test_net_rr_invalid_sl_on_wrong_side_returns_zeros():
    # Long setup but SL above entry → malformed
    r = calculate_net_rr(entry=100.0, sl=105.0, tp=110.0, size=1.0)
    assert r["gross_rr"] == 0.0
    assert r["net_rr"] == 0.0


def test_net_rr_higher_when_maker_only_vs_all_taker():
    """Sanity: switching SL to maker (lower rate) shouldn't *decrease* net RR
    — confirms that the SL leg is the more expensive one under the fee split."""
    r_split = calculate_net_rr(entry=100.0, sl=95.0, tp=110.0, size=1.0,
                                maker_rate=0.0002, taker_rate=0.0004)
    r_all_taker = calculate_net_rr(entry=100.0, sl=95.0, tp=110.0, size=1.0,
                                    maker_rate=0.0004, taker_rate=0.0004)
    assert r_split["net_rr"] > r_all_taker["net_rr"]


# ----------------------------- calculate_expectancy ------------------------- #


def _mock_trade(pnl: float, *, entry: float = 100.0, sl: float = 95.0,
                tp: float = 110.0, size: float = 20.0) -> dict:
    """Risk per trade = (100 - 95) × 20 = $100. Reward at TP = $200 → RR=2."""
    return {
        "pnl": pnl,
        "fill_entry": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "size": size,
    }


def test_expectancy_balanced_10_trades():
    # 5 wins @ +200, 5 losses @ -100 → WR 50%, expectancy +$50, expectancy_r ~ +0.5
    trades = [_mock_trade(200.0) for _ in range(5)] + [_mock_trade(-100.0) for _ in range(5)]
    exp = calculate_expectancy(trades)
    assert exp["n"] == 10
    assert exp["win_rate"] == pytest.approx(0.5)
    assert exp["avg_win"] == pytest.approx(200.0)
    assert exp["avg_loss"] == pytest.approx(100.0)
    assert exp["expectancy_per_trade"] == pytest.approx(50.0)
    # R: wins → +2R, losses → -1R; mean = (5*2 - 5*1)/10 = 0.5
    assert exp["expectancy_r"] == pytest.approx(0.5)


def test_expectancy_empty_returns_safe_dict_no_crash():
    exp = calculate_expectancy([])
    assert exp["n"] == 0
    assert exp["sample_too_small"] is True
    assert exp["expectancy_per_trade"] == 0.0


def test_expectancy_single_trade_flagged_too_small():
    trades = [_mock_trade(200.0)]
    exp = calculate_expectancy(trades)
    assert exp["n"] == 1
    assert exp["sample_too_small"] is True


def test_expectancy_sample_above_threshold_not_flagged():
    trades = [_mock_trade(200.0) for _ in range(20)] + [_mock_trade(-100.0) for _ in range(20)]
    exp = calculate_expectancy(trades)
    assert exp["n"] == 40
    assert exp["sample_too_small"] is False


def test_expectancy_negative_expectancy_flagged():
    # 1 win @ +100, 9 losses @ -100 → WR 10%, expectancy = -80
    trades = [_mock_trade(100.0)] + [_mock_trade(-100.0) for _ in range(9)]
    exp = calculate_expectancy(trades)
    assert exp["expectancy_per_trade"] < 0
    assert exp["negative_expectancy"] is True


def test_expectancy_break_even_wr_consistent_with_avg_rr():
    trades = [_mock_trade(200.0) for _ in range(5)] + [_mock_trade(-100.0) for _ in range(5)]
    exp = calculate_expectancy(trades)
    # With maker/taker split fees are lower than the prior pure-taker model,
    # so avg_net_rr stays close to 2.0 and break-even WR close to 1/3.
    assert 0.33 < exp["break_even_wr"] < 0.34
    assert exp["margin_pct"] > 15


def test_expectancy_maker_taker_better_than_taker_only():
    """Switching from all-taker to maker/taker split should lift net RR and
    therefore lower break-even WR (margin widens)."""
    trades = [_mock_trade(200.0) for _ in range(5)] + [_mock_trade(-100.0) for _ in range(5)]
    exp_split = calculate_expectancy(trades, maker_rate=0.0002, taker_rate=0.0004)
    exp_taker_only = calculate_expectancy(trades, maker_rate=0.0004, taker_rate=0.0004)
    assert exp_split["avg_net_rr"] > exp_taker_only["avg_net_rr"]
    assert exp_split["break_even_wr"] < exp_taker_only["break_even_wr"]
    assert exp_split["margin_pct"] > exp_taker_only["margin_pct"]


def test_format_expectancy_includes_warnings():
    trades = [_mock_trade(200.0)]
    exp = calculate_expectancy(trades)
    rendered = format_expectancy(exp)
    assert "EXPECTANCY ANALYSIS" in rendered
    assert "SAMPLE TOO SMALL" in rendered
