"""Tests for scripts/update_config_expectancy.py — config comment updater."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.update_config_expectancy import (
    _build_comment_block,
    _load_expectancy,
    update_min_rr_comment,
)

SAMPLE_YAML = """\
# Header comment that must be preserved.

structure:
  swing_lookback_5m: 3
  atr_period: 14

setup_sweep_fvg:
  sweep_lookback_bars: 30
  fee_rate_round_trip: 0.0008
  # Min RR — bu altındaysa sinyal atılmaz. 2.5 → break-even WR ≈ 28.6%, gözlemlenen ~37.5% historical WR ile pozitif beklenti
  min_rr: 2.5

risk:
  max_open_positions: 2
"""


@pytest.fixture
def tmp_config(tmp_path: Path) -> Path:
    p = tmp_path / "strategy_params.yaml"
    p.write_text(SAMPLE_YAML, encoding="utf-8")
    return p


@pytest.fixture
def expectancy_block() -> str:
    exp = {
        "n": 6,
        "win_rate": 0.5,
        "break_even_wr": 0.286,
        "margin_pct": 21.4,
        "expectancy_per_trade": 62.51,
        "expectancy_r": 0.38,
    }
    return _build_comment_block(exp, "btc_run7.json")


def test_min_rr_value_preserved(tmp_config: Path, expectancy_block: str):
    update_min_rr_comment(tmp_config, expectancy_block)
    text = tmp_config.read_text(encoding="utf-8")
    assert "min_rr: 2.5" in text


def test_other_yaml_content_preserved(tmp_config: Path, expectancy_block: str):
    update_min_rr_comment(tmp_config, expectancy_block)
    text = tmp_config.read_text(encoding="utf-8")
    # Header + sibling keys + other section preserved
    assert "Header comment that must be preserved" in text
    assert "swing_lookback_5m" in text
    assert "fee_rate_round_trip" in text
    assert "max_open_positions" in text


def test_auto_marker_present_after_update(tmp_config: Path, expectancy_block: str):
    update_min_rr_comment(tmp_config, expectancy_block)
    text = tmp_config.read_text(encoding="utf-8")
    assert "Auto-updated" in text
    assert "Expectancy:" in text
    assert "N=6" in text


def test_idempotent(tmp_config: Path, expectancy_block: str):
    update_min_rr_comment(tmp_config, expectancy_block)
    first = tmp_config.read_text(encoding="utf-8")
    update_min_rr_comment(tmp_config, expectancy_block)
    second = tmp_config.read_text(encoding="utf-8")
    assert first == second


def test_load_expectancy_falls_back_to_compute(tmp_path: Path):
    """Backtest JSON without precomputed 'expectancy' should be computed from trades."""
    bt = tmp_path / "bt.json"
    bt.write_text(json.dumps({
        "trades": [
            {"pnl": 200.0, "fill_entry": 100.0, "stop_loss": 95.0,
             "take_profit": 110.0, "size": 20.0},
            {"pnl": -100.0, "fill_entry": 100.0, "stop_loss": 95.0,
             "take_profit": 110.0, "size": 20.0},
        ],
    }), encoding="utf-8")
    exp = _load_expectancy(bt, maker_rate=0.0002, taker_rate=0.0004)
    assert exp["n"] == 2
    assert exp["win_rate"] == pytest.approx(0.5)


def test_load_expectancy_uses_precomputed_if_present(tmp_path: Path):
    """If JSON carries expectancy, it should be used as-is."""
    bt = tmp_path / "bt.json"
    bt.write_text(json.dumps({
        "trades": [],
        "expectancy": {"n": 99, "win_rate": 0.42, "break_even_wr": 0.3,
                       "margin_pct": 12.0, "expectancy_per_trade": 10.0,
                       "expectancy_r": 0.2},
    }), encoding="utf-8")
    exp = _load_expectancy(bt, maker_rate=0.0002, taker_rate=0.0004)
    assert exp["n"] == 99
