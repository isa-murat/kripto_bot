"""Tests for src.strategies.ote.

Negative path coverage + SetupParams + a synthetic positive integration test
that wires up HTF bull bias + 5m MSS + retrace into the OTE zone.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import polars as pl

from src.config import StrategyParams
from src.ict.structure import Trend
from src.strategies.ote import SETUP_NAME, SetupParams, evaluate

UTC = timezone.utc


def _bars(rows: list[tuple[float, float, float, float]],
          start: datetime | None = None,
          minutes_per_bar: int = 5) -> pl.DataFrame:
    n = len(rows)
    start = start or datetime(2026, 1, 1, tzinfo=UTC)
    return pl.DataFrame({
        "ts": [start + timedelta(minutes=minutes_per_bar * i) for i in range(n)],
        "open":   [float(r[0]) for r in rows],
        "high":   [float(r[1]) for r in rows],
        "low":    [float(r[2]) for r in rows],
        "close":  [float(r[3]) for r in rows],
        "volume": [100.0] * n,
    }).with_columns(pl.col("ts").cast(pl.Datetime("ms", time_zone="UTC")))


# ============================== empty / degenerate ========================== #


def test_evaluate_returns_none_on_empty_dataframes():
    df = _bars([])
    assert evaluate(symbol="BTCUSDT", df_ltf=df, df_htf=df) is None


def test_evaluate_returns_none_when_only_htf_empty():
    htf = _bars([])
    ltf = _bars([(100, 102, 98, 101)] * 50)
    assert evaluate(symbol="BTCUSDT", df_ltf=ltf, df_htf=htf) is None


# ============================== bias filter ================================= #


def test_no_signal_when_htf_bias_is_neutral():
    """Flat HTF → no swings → bias NEUTRAL → no signal."""
    htf = _bars([(100, 101, 99, 100)] * 30, minutes_per_bar=60)
    ltf = _bars([(100, 102, 98, 101)] * 50)
    params = SetupParams(require_killzone=False)
    assert evaluate(symbol="BTCUSDT", df_ltf=ltf, df_htf=htf, params=params) is None


# ============================== killzone filter ============================= #


def test_killzone_default_is_off_per_F06():
    """Per F-06, OTE defaults require_killzone=False (Forex killzones don't transfer)."""
    p = SetupParams()
    assert p.require_killzone is False


def test_killzone_can_be_enabled_explicitly():
    """If require_killzone=True is set, the time filter blocks bars outside
    London/NY sessions even with bull data."""
    htf = _bars([(100, 101, 99, 100)] * 30,
                start=datetime(2026, 5, 11, 0, 0, tzinfo=UTC),
                minutes_per_bar=60)
    # 03:00 TR = 00:00 UTC — outside both London (10-13 TR) and NY (15-19 TR) killzones
    ltf = _bars([(100, 102, 98, 101)] * 50,
                start=datetime(2026, 5, 11, 0, 0, tzinfo=UTC))
    params = SetupParams(require_killzone=True)
    assert evaluate(symbol="BTCUSDT", df_ltf=ltf, df_htf=htf, params=params) is None


# ============================== MSS filter ================================== #


def test_no_signal_when_no_mss_on_ltf():
    """Bull HTF bias but flat LTF → no MSS → no signal."""
    htf = _bullish_htf_bars()
    ltf = _bars([(100, 101, 99, 100)] * 50)
    params = SetupParams(require_killzone=False)
    assert evaluate(symbol="BTCUSDT", df_ltf=ltf, df_htf=htf, params=params) is None


# ============================== params construction ======================== #


def test_setup_params_defaults():
    p = SetupParams()
    assert p.swing_lookback == 3
    assert p.displacement_atr_mult == 1.5
    assert p.mss_lookback_bars == 30
    assert p.max_leg_lookback_bars == 50
    assert p.min_leg_atr_mult == 1.5
    assert p.fib_zone_low == 0.618
    assert p.fib_zone_high == 0.786
    assert p.fib_entry_target == 0.705
    assert p.sl_buffer_atr == 0.30
    assert p.min_sl_distance_atr == 0.50
    assert p.tp_r_multiple == 2.0
    assert p.max_fee_to_reward_ratio == 0.30
    assert p.fee_rate_round_trip == 0.0008
    assert p.require_killzone is False
    assert p.bias_zone_required is False


def test_setup_params_from_strategy_params_reads_yaml():
    sp = StrategyParams(
        structure={"swing_lookback_5m": 5, "swing_lookback_1h": 7,
                   "displacement_atr_mult": 2.0},
        bias={"range_lookback_swings": 6, "premium_threshold": 0.62,
              "discount_threshold": 0.38},
        setup_ote={
            "mss_lookback_bars": 40,
            "max_leg_lookback_bars": 60,
            "min_leg_atr_mult": 2.0,
            "fib_zone_low": 0.5,
            "fib_zone_high": 0.8,
            "fib_entry_target": 0.65,
            "sl_buffer_atr": 0.4,
            "min_sl_distance_atr": 0.7,
            "tp_r_multiple": 3.0,
            "max_fee_to_reward_ratio": 0.25,
            "fee_rate_round_trip": 0.0006,
            "require_killzone": True,
            "bias_zone_required": True,
        },
    )
    p = SetupParams.from_strategy_params(sp)
    assert p.swing_lookback == 5
    assert p.htf_swing_lookback == 7
    assert p.displacement_atr_mult == 2.0
    assert p.mss_lookback_bars == 40
    assert p.max_leg_lookback_bars == 60
    assert p.min_leg_atr_mult == 2.0
    assert p.fib_zone_low == 0.5
    assert p.fib_zone_high == 0.8
    assert p.fib_entry_target == 0.65
    assert p.sl_buffer_atr == 0.4
    assert p.min_sl_distance_atr == 0.7
    assert p.tp_r_multiple == 3.0
    assert p.max_fee_to_reward_ratio == 0.25
    assert p.fee_rate_round_trip == 0.0006
    assert p.require_killzone is True
    assert p.bias_zone_required is True
    assert p.htf_range_lookback_swings == 6
    assert p.htf_premium_threshold == 0.62
    assert p.htf_discount_threshold == 0.38


def test_setup_params_from_empty_strategy_params_uses_defaults():
    sp = StrategyParams()
    p = SetupParams.from_strategy_params(sp)
    assert p.mss_lookback_bars == 30
    assert p.tp_r_multiple == 2.0
    assert p.fib_entry_target == 0.705
    assert p.require_killzone is False


# ============================== synthetic helpers =========================== #


def _bullish_htf_bars() -> pl.DataFrame:
    """30 × 1h bars with discrete fractal peaks at i=5, 15, 25 — each higher
    than the last. Lookback=5 detects swing highs at i=5 (high=105) and i=15
    (high=108); the second peak breaks the first → bullish BOS at i=15 (and
    again at i=25 breaking 108). `current_trend` at bar 29 = BULL.
    """
    flat = (100, 100, 100, 100)
    peak1 = (100, 105, 100, 103)
    peak2 = (100, 108, 100, 105)
    peak3 = (100, 112, 100, 108)
    plateau_after_peak1 = (100, 103, 100, 100)
    plateau_after_peak2 = (100, 103, 100, 100)
    plateau_after_peak3 = (100, 105, 100, 100)
    rows = [
        flat, flat, flat, flat, flat,                                         # 0-4
        peak1,                                                                # 5
        plateau_after_peak1, plateau_after_peak1, plateau_after_peak1,
        plateau_after_peak1,                                                  # 6-9
        flat, flat, flat, flat, flat,                                         # 10-14
        peak2,                                                                # 15
        plateau_after_peak2, plateau_after_peak2, plateau_after_peak2,
        plateau_after_peak2,                                                  # 16-19
        flat, flat, flat, flat, flat,                                         # 20-24
        peak3,                                                                # 25
        plateau_after_peak3, plateau_after_peak3, plateau_after_peak3,
        plateau_after_peak3,                                                  # 26-29
    ]
    return _bars(rows, minutes_per_bar=60)


# ============================== positive integration ======================= #


def test_positive_bull_ote_signal_in_zone():
    """Bull HTF bias + LTF: dip then strong up-break (MSS) + retrace into OTE zone.

    The synthetic LTF is constructed so:
      - bars 0-6: ranging around 100, with a clear swing high at i=3 (high=115)
      - bars 7-12: monotonic down — creates a leg base at low=72
      - bar 13: huge bull body 75→120 breaks above 115 → MSS BULL
      - bars 14-15: continue up, leg apex reaches 140
      - bars 16-19: gradual retrace, no bar's low enters the OTE zone yet
      - bar 20: low dips to 92 (inside zone [86.55, 97.98]) → first-touch entry
    """
    htf = _bullish_htf_bars()
    ltf_rows = [
        # (open, high, low, close)
        (100, 100, 100, 100),   # 0
        (100, 100, 100, 100),   # 1
        (100, 100, 100, 100),   # 2
        (100, 115, 100, 114),   # 3  swing high candidate (high=115)
        (114, 114, 100, 102),   # 4
        (102, 102, 100, 100),   # 5
        (100, 100, 100, 100),   # 6  i=3 swing high confirms here (lookback=3)
        (100, 100, 95, 97),     # 7
        (97, 97, 90, 92),       # 8
        (92, 92, 85, 87),       # 9
        (87, 87, 80, 82),       # 10
        (82, 82, 75, 77),       # 11
        (77, 77, 72, 75),       # 12  leg_base low=72
        (75, 125, 75, 120),     # 13  MSS BULL — body 45 >> 1.5×ATR
        (120, 135, 120, 133),   # 14
        (133, 140, 130, 135),   # 15  leg_apex high=140
        (135, 138, 133, 136),   # 16
        (136, 136, 128, 130),   # 17
        (130, 130, 122, 125),   # 18
        (125, 125, 118, 121),   # 19
        (121, 121, 92, 102),    # 20  low=92 first-touches zone [86.55, 97.98]
    ]
    ltf = _bars(ltf_rows)
    params = SetupParams(require_killzone=False)

    sig = evaluate(symbol="BTCUSDT", df_ltf=ltf, df_htf=htf, params=params)
    assert sig is not None, "expected a BULL OTE signal"
    assert sig.side == Trend.BULL
    assert sig.setup_name == SETUP_NAME
    # Entry near the 0.705 sweet spot of [72, 140] → 140 - 0.705*68 ≈ 92.06
    assert 91.5 < sig.entry_price < 92.5
    # SL below leg base 72 with ATR buffer
    assert sig.stop_loss < 72.0
    # TP = entry + 2R*risk (2R baseline)
    risk = sig.entry_price - sig.stop_loss
    expected_tp = sig.entry_price + 2.0 * risk
    assert abs(sig.take_profit - expected_tp) < 1e-6
    # RR should equal tp_r_multiple
    assert abs(sig.rr - 2.0) < 1e-6
    # Meta carries the leg + zone for diagnostics
    assert sig.meta["leg_base_price"] == 72.0
    assert sig.meta["leg_apex_price"] == 140.0
    assert sig.meta["fib_entry_target"] == 0.705
    assert sig.meta["htf_trend"] == Trend.BULL.value


def test_no_signal_when_zone_already_consumed_before_current_bar():
    """If a prior bar's low already touched the zone, the first-touch guard
    rejects the current-bar tap (we already missed the entry)."""
    htf = _bullish_htf_bars()
    ltf_rows = [
        (100, 100, 100, 100),   # 0
        (100, 100, 100, 100),   # 1
        (100, 100, 100, 100),   # 2
        (100, 115, 100, 114),   # 3
        (114, 114, 100, 102),   # 4
        (102, 102, 100, 100),   # 5
        (100, 100, 100, 100),   # 6
        (100, 100, 95, 97),     # 7
        (97, 97, 90, 92),       # 8
        (92, 92, 85, 87),       # 9
        (87, 87, 80, 82),       # 10
        (82, 82, 75, 77),       # 11
        (77, 77, 72, 75),       # 12
        (75, 125, 75, 120),     # 13  MSS
        (120, 135, 120, 133),   # 14
        (133, 140, 130, 135),   # 15  leg apex
        (135, 138, 95, 136),    # 16  ← prior bar already tapped the zone (low=95)
        (136, 136, 128, 130),   # 17
        (130, 130, 122, 125),   # 18
        (125, 125, 118, 121),   # 19
        (121, 121, 92, 102),    # 20  ← current bar tap, but late
    ]
    ltf = _bars(ltf_rows)
    params = SetupParams(require_killzone=False)
    assert evaluate(symbol="BTCUSDT", df_ltf=ltf, df_htf=htf, params=params) is None


def test_no_signal_when_retrace_not_yet_in_zone():
    """Bar 20 low stays above zone_high — price hasn't retraced far enough yet."""
    htf = _bullish_htf_bars()
    ltf_rows = [
        (100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 100, 100),
        (100, 115, 100, 114), (114, 114, 100, 102), (102, 102, 100, 100),
        (100, 100, 100, 100), (100, 100, 95, 97), (97, 97, 90, 92),
        (92, 92, 85, 87), (87, 87, 80, 82), (82, 82, 75, 77),
        (77, 77, 72, 75),
        (75, 125, 75, 120), (120, 135, 120, 133), (133, 140, 130, 135),
        (135, 138, 133, 136), (136, 136, 128, 130), (130, 130, 122, 125),
        (125, 125, 118, 121),
        (121, 121, 110, 115),   # 20  ← low=110 still above zone_high=97.98
    ]
    ltf = _bars(ltf_rows)
    params = SetupParams(require_killzone=False)
    assert evaluate(symbol="BTCUSDT", df_ltf=ltf, df_htf=htf, params=params) is None
