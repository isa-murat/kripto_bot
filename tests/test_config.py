"""Smoke test: configuration loads correctly."""

from __future__ import annotations


def test_settings_loads(settings):
    assert settings.exchange.name == "binance"
    assert "BTCUSDT" in settings.symbols
    assert settings.timeframes.entry == "5m"
    assert settings.timeframes.bias == "1h"


def test_killzones_parsed(settings):
    kzs = settings.filters.killzones
    london = next(kz for kz in kzs if kz.name == "london")
    assert london.start_utc == "07:00"
    assert london.end_utc == "10:00"
    assert london.enabled is True
    ny = next(kz for kz in kzs if kz.name == "new_york")
    assert ny.start_utc == "12:00"
    assert ny.enabled is True
    asia = next(kz for kz in kzs if kz.name == "asia")
    assert asia.enabled is False


def test_strategy_params_loads(strategy_params):
    assert "swing_lookback_5m" in strategy_params.structure
    assert "fvg_min_atr_mult" in strategy_params.poi
    assert strategy_params.setup_sweep_fvg["min_rr"] == 2.0
    assert strategy_params.setup_sweep_fvg["max_rr"] == 4.0


def test_env_defaults(env):
    assert env.paper_initial_equity > 0
    assert 0 < env.paper_risk_per_trade < 1
