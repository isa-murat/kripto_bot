"""Update the `min_rr` comment block in strategy_params.yaml from a backtest JSON.

Reads a backtest result JSON (or auto-picks the newest in data/backtest/),
computes expectancy if the file doesn't already carry it, and rewrites the
comment lines preceding `min_rr` under `setup_sweep_fvg`. The value of `min_rr`
itself is never modified. Idempotent — running twice on the same input
produces the same file.

Usage:
    python -m scripts.update_config_expectancy
    python -m scripts.update_config_expectancy --backtest data/backtest/btc_run7.json
    python -m scripts.update_config_expectancy --backtest <path> --config <path>
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import typer

from src.config import CONFIG_DIR, REPO_ROOT
from src.utils.rr_metrics import calculate_expectancy

app = typer.Typer(add_completion=False, help=__doc__)

DEFAULT_BACKTEST_DIR = REPO_ROOT / "data" / "backtest"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "strategy_params.yaml"

# Marker line that identifies our auto-generated block. Lines below this (up
# to but not including the `min_rr:` line) are owned by this script and will
# be replaced on every run. Lines above are user-owned and preserved.
AUTO_MARKER = "Auto-updated"


def _newest_backtest_json(directory: Path) -> Path:
    files = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No backtest JSON found in {directory}")
    return files[0]


def _load_expectancy(backtest_path: Path, maker_rate: float, taker_rate: float) -> dict:
    """Return expectancy dict for a backtest JSON. Compute it if missing.

    Supports two shapes:
    - single-symbol runner output: top-level `expectancy` + `trades`
    - multi-symbol script output:  `pooled.expectancy` + `pooled.trades`
    """
    payload = json.loads(backtest_path.read_text(encoding="utf-8"))
    exp = payload.get("expectancy")
    if exp is None:
        exp = payload.get("pooled", {}).get("expectancy")
    if exp is None:
        trades = payload.get("trades") or payload.get("pooled", {}).get("trades", [])
        exp = calculate_expectancy(
            trades, maker_rate=maker_rate, taker_rate=taker_rate,
        )
    return exp


def _build_comment_block(exp: dict, backtest_filename: str) -> str:
    """Render the multi-line YAML comment to live above `min_rr:`.

    Lines start with no leading space — ruamel adds its own indentation and
    leading `# ` per line via `yaml_set_comment_before_after_key`.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    wr = exp["win_rate"] * 100
    bewr = exp["break_even_wr"] * 100
    margin = exp["margin_pct"]
    expectancy = exp["expectancy_per_trade"]
    expectancy_r = exp["expectancy_r"]
    n = exp["n"]
    lines = [
        "Min RR — bu altındaysa sinyal atılmaz.",
        f"{AUTO_MARKER} {today} from {backtest_filename}:",
        f"  N={n} trades | Observed WR {wr:.1f}% | Break-even WR {bewr:.1f}% | Margin {margin:+.1f}pp",
        f"  Expectancy: ${expectancy:+.2f}/trade ({expectancy_r:+.2f}R)",
    ]
    return "\n".join(lines)


MIN_RR_LINE = re.compile(r"^(?P<indent>[ \t]+)min_rr\s*:")


def update_min_rr_comment(
    config_path: Path,
    comment_block: str,
) -> None:
    """Replace the comment lines directly above `min_rr:` with `comment_block`.

    Line-based rewrite (not full YAML round-trip): strip every contiguous
    comment line preceding `min_rr:`, then insert `comment_block` lines with
    the matching indent. Idempotent — running twice yields the same file.
    The `min_rr:` value itself is never touched, nor is any other line.
    Chose this over ruamel's set_comment API because that API appends rather
    than replaces, defeating idempotency.
    """
    text = config_path.read_text(encoding="utf-8")
    had_trailing_newline = text.endswith("\n")
    lines = text.splitlines()

    idx = None
    indent = ""
    for i, ln in enumerate(lines):
        m = MIN_RR_LINE.match(ln)
        if m:
            idx = i
            indent = m.group("indent")
            break
    if idx is None:
        raise KeyError("strategy_params.yaml missing `min_rr:` under any block")

    start = idx
    while start > 0 and lines[start - 1].lstrip().startswith("#"):
        start -= 1

    new_comment_lines = [f"{indent}# {line}" for line in comment_block.split("\n")]
    result = lines[:start] + new_comment_lines + lines[idx:]
    out = "\n".join(result) + ("\n" if had_trailing_newline else "")
    config_path.write_text(out, encoding="utf-8", newline="\n")


@app.command()
def main(
    backtest: Path | None = typer.Option(
        None,
        help="Backtest JSON path (default: newest in data/backtest/).",
    ),
    config: Path = typer.Option(
        DEFAULT_CONFIG_PATH,
        help="Path to strategy_params.yaml.",
    ),
    maker_rate: float = typer.Option(
        0.0002,
        help="One-way maker fee (used only if backtest JSON lacks expectancy).",
    ),
    taker_rate: float = typer.Option(
        0.0004,
        help="One-way taker fee (used only if backtest JSON lacks expectancy).",
    ),
) -> None:
    backtest_path = backtest or _newest_backtest_json(DEFAULT_BACKTEST_DIR)
    if not backtest_path.exists():
        raise typer.BadParameter(f"Backtest file not found: {backtest_path}")
    if not config.exists():
        raise typer.BadParameter(f"Config file not found: {config}")

    exp = _load_expectancy(backtest_path, maker_rate=maker_rate, taker_rate=taker_rate)
    comment_block = _build_comment_block(exp, backtest_path.name)
    update_min_rr_comment(config, comment_block)

    typer.echo(f"Updated {config} from {backtest_path.name}")
    typer.echo("-" * 56)
    typer.echo(comment_block)
    typer.echo("-" * 56)


if __name__ == "__main__":
    app()
