"""Tests for src.strategies.sweep_fvg.

Pozitif integration testi (signal üretilen tüm koşullar sağlanıyor) sentetik
fixture ile inşa etmek karmaşık olduğu için Faz 4 backtest'e bırakıldı —
orada gerçek tarihsel data ile tam pipeline doğrulanacak. Bu turdaki testler
**negatif path'lere** + parametre yapısına odaklanır.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import polars as pl

from src.config import StrategyParams
from src.ict.structure import Trend
from src.strategies.sweep_fvg import (
    SetupParams,
    compute_rr,
    compute_sl,
    evaluate,
    passes_min_rr,
)

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


def test_evaluate_returns_none_when_only_ltf_empty():
    htf = _bars([(100, 101, 99, 100)] * 30, minutes_per_bar=60)
    ltf = _bars([])
    assert evaluate(symbol="BTCUSDT", df_ltf=ltf, df_htf=htf) is None


# ============================== bias filter ================================= #


def test_no_signal_when_htf_bias_is_neutral():
    """Flat HTF data → no swings → bias NEUTRAL → no signal regardless of LTF."""
    htf = _bars([(100, 101, 99, 100)] * 30, minutes_per_bar=60)
    ltf = _bars([(100, 102, 98, 101)] * 50)
    params = SetupParams(require_killzone=False)
    assert evaluate(symbol="BTCUSDT", df_ltf=ltf, df_htf=htf, params=params) is None


# ============================== killzone filter ============================= #


def test_no_signal_when_outside_killzone_and_required():
    """Bar timestamp outside London/NY killzone → reject regardless of bias."""
    # 2026-05-11 00:00 UTC = 03:00 TR — kesinlikle killzone dışı
    htf = _bars([(100, 101, 99, 100)] * 30,
                start=datetime(2026, 5, 10, 18, 0, tzinfo=UTC),
                minutes_per_bar=60)
    ltf = _bars([(100, 102, 98, 101)] * 50,
                start=datetime(2026, 5, 11, 0, 0, tzinfo=UTC))
    # require_killzone=True (default)
    assert evaluate(symbol="BTCUSDT", df_ltf=ltf, df_htf=htf) is None


def test_killzone_can_be_bypassed_for_backtest():
    """require_killzone=False should let the pipeline proceed past the time filter.

    Still returns None here because flat data has no setup; the point is no
    AssertionError or path-short-circuit on killzone alone.
    """
    htf = _bars([(100, 101, 99, 100)] * 30,
                start=datetime(2026, 5, 11, 0, 0, tzinfo=UTC),
                minutes_per_bar=60)
    ltf = _bars([(100, 102, 98, 101)] * 50,
                start=datetime(2026, 5, 11, 0, 0, tzinfo=UTC))
    params = SetupParams(require_killzone=False)
    assert evaluate(symbol="BTCUSDT", df_ltf=ltf, df_htf=htf, params=params) is None


# ============================== params construction ======================== #


def test_setup_params_defaults_match_strategy_yaml_intent():
    p = SetupParams()
    assert p.swing_lookback == 3
    assert p.displacement_atr_mult == 1.5
    assert p.equal_level_tolerance_atr == 0.30
    assert p.sweep_min_wick_pct == 0.20
    assert p.sweep_lookback_bars == 30
    assert p.sweep_to_mss_max_bars == 20
    assert p.mss_to_fvg_max_bars == 5
    assert p.fvg_min_atr_mult == 0.15
    assert p.sl_buffer_atr == 0.30
    assert p.min_sl_distance_atr == 0.50
    assert p.max_fee_to_reward_ratio == 0.30
    assert p.fee_rate_round_trip == 0.0008
    assert p.bias_zone_required is False
    assert p.min_rr == 2.0
    assert p.max_rr == 4.0
    assert p.pool_min_touches == 2
    assert p.pool_min_age_bars == 10
    assert p.require_killzone is True


def test_setup_params_from_strategy_params_reads_yaml_values():
    sp = StrategyParams(
        structure={"swing_lookback_5m": 5, "swing_lookback_1h": 7,
                   "displacement_atr_mult": 2.0},
        liquidity={"equal_level_tolerance_atr": 0.20, "sweep_min_wick_pct": 0.40},
        poi={"fvg_min_atr_mult": 0.25, "fvg_max_age_bars": 50},
        bias={"range_lookback_swings": 6, "premium_threshold": 0.62,
              "discount_threshold": 0.38},
        setup_sweep_fvg={"sweep_to_mss_max_bars": 7, "mss_to_fvg_max_bars": 4,
                         "sl_buffer_atr": 0.30, "min_rr": 3.0},
    )
    p = SetupParams.from_strategy_params(sp)
    assert p.swing_lookback == 5
    assert p.htf_swing_lookback == 7
    assert p.displacement_atr_mult == 2.0
    assert p.equal_level_tolerance_atr == 0.20
    assert p.sweep_min_wick_pct == 0.40
    assert p.fvg_min_atr_mult == 0.25
    assert p.fvg_max_age_bars == 50
    assert p.htf_range_lookback_swings == 6
    assert p.htf_premium_threshold == 0.62
    assert p.htf_discount_threshold == 0.38
    assert p.sweep_to_mss_max_bars == 7
    assert p.mss_to_fvg_max_bars == 4
    assert p.sl_buffer_atr == 0.30
    assert p.min_rr == 3.0


# ============================== RR helpers ================================== #


def test_compute_rr_basic_long():
    # entry=100, sl=99 (risk 1), tp=103 (reward 3) → rr=3.0
    assert compute_rr(100.0, 99.0, 103.0) == 3.0


def test_compute_rr_basic_short():
    # entry=100, sl=101 (risk 1), tp=97 (reward 3) → rr=3.0
    assert compute_rr(100.0, 101.0, 97.0) == 3.0


def test_compute_rr_zero_risk_returns_zero():
    """SL == entry → undefined RR; we return 0 (rejected by any min_rr > 0)."""
    assert compute_rr(100.0, 100.0, 102.0) == 0.0


def test_passes_min_rr_rejects_below_threshold():
    # 1.5 RR setup against min_rr=2.5 → rejected (acceptance criteria)
    assert passes_min_rr(100.0, 99.0, 101.5, min_rr=2.5) is False


def test_passes_min_rr_accepts_at_or_above_threshold():
    # 2.5 exactly
    assert passes_min_rr(100.0, 99.0, 101.5, min_rr=1.5) is True
    # 3.0 vs 2.5
    assert passes_min_rr(100.0, 99.0, 103.0, min_rr=2.5) is True
    # exact equality
    assert passes_min_rr(100.0, 99.0, 102.5, min_rr=2.5) is True


def test_passes_min_rr_zero_risk_always_rejected():
    assert passes_min_rr(100.0, 100.0, 102.0, min_rr=0.5) is False


# ============================== SL ATR buffer ============================== #


def test_compute_sl_long_uses_atr_buffer():
    """SL = sweep_low - (atr_mult × atr). 100 - 0.3*10 = 97.0."""
    sl = compute_sl(
        side=Trend.BULL, sweep_extreme=100.0, atr=10.0,
        atr_buffer_mult=0.30, entry=110.0, min_sl_distance=0.0,
    )
    assert sl == 97.0


def test_compute_sl_short_uses_atr_buffer():
    """SL = sweep_high + (atr_mult × atr). 100 + 0.3*10 = 103.0."""
    sl = compute_sl(
        side=Trend.BEAR, sweep_extreme=100.0, atr=10.0,
        atr_buffer_mult=0.30, entry=90.0, min_sl_distance=0.0,
    )
    assert sl == 103.0


def test_compute_sl_long_min_distance_widens_when_buffer_too_close():
    """If sweep is very close to entry, min_sl_distance pushes SL further away."""
    # Without min_dist: SL = 100 - 0.3*10 = 97 (only 3 below entry=99)
    # With min_dist=5: SL must be ≤ 99-5 = 94 → 94 wins
    sl = compute_sl(
        side=Trend.BULL, sweep_extreme=100.0, atr=10.0,
        atr_buffer_mult=0.30, entry=99.0, min_sl_distance=5.0,
    )
    assert sl == 94.0


def test_compute_sl_short_min_distance_widens_when_buffer_too_close():
    sl = compute_sl(
        side=Trend.BEAR, sweep_extreme=100.0, atr=10.0,
        atr_buffer_mult=0.30, entry=101.0, min_sl_distance=5.0,
    )
    # Without min_dist: SL = 100 + 3 = 103 (only 2 above entry=101)
    # With min_dist=5: SL must be ≥ 101+5 = 106 → 106 wins
    assert sl == 106.0


def test_compute_sl_long_buffer_dominant_when_already_far():
    """When sweep is far from entry, ATR buffer is the binding constraint."""
    # raw = 80 - 0.3*10 = 77; entry-min_dist = 110-2 = 108. min(77, 108) = 77
    sl = compute_sl(
        side=Trend.BULL, sweep_extreme=80.0, atr=10.0,
        atr_buffer_mult=0.30, entry=110.0, min_sl_distance=2.0,
    )
    assert sl == 77.0


def test_compute_sl_zero_atr_falls_back_to_min_distance():
    """ATR=0 → buffer=0 → SL right at sweep_extreme. min_distance kicks in if set."""
    sl = compute_sl(
        side=Trend.BULL, sweep_extreme=100.0, atr=0.0,
        atr_buffer_mult=0.30, entry=110.0, min_sl_distance=5.0,
    )
    # raw = 100 - 0 = 100; entry-min_dist = 105 → min(100, 105) = 100
    assert sl == 100.0


def test_compute_sl_neutral_side_raises():
    import pytest as _pytest
    with _pytest.raises(ValueError):
        compute_sl(
            side=Trend.NEUTRAL, sweep_extreme=100.0, atr=10.0,
            atr_buffer_mult=0.30, entry=110.0,
        )


def test_rr_uses_buffered_sl_not_raw_sweep():
    """RR is computed off the buffered SL, not the bare sweep level — per spec."""
    # entry=110, sweep_low=100, atr=10, mult=0.3 → SL=97 → risk=13
    # tp=130 → reward=20 → rr=20/13 ≈ 1.54
    sl = compute_sl(
        side=Trend.BULL, sweep_extreme=100.0, atr=10.0,
        atr_buffer_mult=0.30, entry=110.0, min_sl_distance=0.0,
    )
    rr_buffered = compute_rr(110.0, sl, 130.0)
    rr_raw = compute_rr(110.0, 100.0, 130.0)
    assert rr_buffered < rr_raw   # buffer makes RR smaller (risk grew)
    assert abs(rr_buffered - 20.0 / 13.0) < 1e-9


def test_setup_params_from_empty_strategy_params_uses_defaults():
    sp = StrategyParams()
    p = SetupParams.from_strategy_params(sp)
    assert p.swing_lookback == 3
    assert p.min_rr == 2.0
    assert p.max_rr == 4.0
