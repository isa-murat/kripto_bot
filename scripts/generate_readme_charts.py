"""Generate README charts from backtest JSONs.

Outputs to docs/images/:
  - run13_per_symbol_margin.png
  - eth_is_vs_oos.png
  - eth_equity_curves.png
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter
from matplotlib.ticker import FuncFormatter

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "backtest"
OUT = ROOT / "docs" / "images"
OUT.mkdir(parents=True, exist_ok=True)

# Clean style
plt.rcParams.update({
    "figure.dpi": 110,
    "savefig.dpi": 130,
    "savefig.bbox": "tight",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "font.size": 10,
    "axes.titlesize": 12,
    "axes.titleweight": "bold",
})

POS = "#2e7d32"   # green
NEG = "#c62828"   # red
ACC = "#1565c0"   # blue accent
GREY = "#9e9e9e"


def _load(name: str) -> dict:
    return json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Chart 1: Run13 per-symbol margin (pooled overlay)
# ---------------------------------------------------------------------------

def chart_per_symbol_margin() -> None:
    d = _load("multi_run13")
    pool = d["pooled"]["expectancy"]
    rows = sorted(
        d["per_symbol"],
        key=lambda r: r["expectancy"]["margin_pct"],
    )
    symbols = [r["symbol"].replace("USDT", "") for r in rows]
    margins = [r["expectancy"]["margin_pct"] for r in rows]
    ns = [r["expectancy"]["n"] for r in rows]
    colors = [POS if m > 0 else NEG for m in margins]

    fig, ax = plt.subplots(figsize=(8.2, 4.2))
    bars = ax.barh(symbols, margins, color=colors, edgecolor="white", linewidth=0.5)
    for bar, m, n in zip(bars, margins, ns):
        x = bar.get_width()
        ha = "left" if x >= 0 else "right"
        offset = 0.3 if x >= 0 else -0.3
        ax.text(x + offset, bar.get_y() + bar.get_height() / 2,
                f"{m:+.2f}pp  (N={n})", va="center", ha=ha, fontsize=9)

    pooled_margin = pool["margin_pct"]
    ax.axvline(pooled_margin, color=ACC, linestyle="--", linewidth=1.5,
               label=f"Pooled: {pooled_margin:+.2f}pp (N={pool['n']})")
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_xlabel("Margin (% points over break-even WR)")
    ax.set_title("Run13 — Sweep+FVG margin per symbol (2025-11 → 2026-05, N=309)")
    ax.legend(loc="lower right", framealpha=0.95)
    ax.set_xlim(min(margins) - 5, max(margins) + 5)

    out = OUT / "run13_per_symbol_margin.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out.relative_to(ROOT)}")


# ---------------------------------------------------------------------------
# Chart 2: ETH IS vs OOS comparison (Run13 vs Run15)
# ---------------------------------------------------------------------------

def chart_eth_is_vs_oos() -> None:
    is_run = _load("ETHUSDT_run13")["expectancy"]
    oos_run = _load("ETHUSDT_run15")["expectancy"]

    metrics = [
        ("Win rate (%)",           is_run["win_rate"] * 100,           oos_run["win_rate"] * 100,           "{:.1f}%"),
        ("Margin (pp)",            is_run["margin_pct"],               oos_run["margin_pct"],               "{:+.2f}pp"),
        ("Expectancy / trade ($)", is_run["expectancy_per_trade"],     oos_run["expectancy_per_trade"],     "${:+.2f}"),
        ("Expectancy / R",         is_run["expectancy_r"],             oos_run["expectancy_r"],             "{:+.3f}R"),
    ]

    fig, axes = plt.subplots(1, 4, figsize=(11.5, 3.6))
    for ax, (label, is_v, oos_v, fmt) in zip(axes, metrics):
        bars = ax.bar(["IS\nN=67", "OOS\nN=86"], [is_v, oos_v],
                      color=[POS if is_v > 0 else NEG, POS if oos_v > 0 else NEG],
                      edgecolor="white", linewidth=0.8)
        for bar, v in zip(bars, [is_v, oos_v]):
            height = bar.get_height()
            offset_dir = 1 if height >= 0 else -1
            ax.annotate(fmt.format(v),
                        xy=(bar.get_x() + bar.get_width() / 2, height),
                        xytext=(0, 6 * offset_dir), textcoords="offset points",
                        ha="center",
                        va="bottom" if offset_dir > 0 else "top",
                        fontsize=9, weight="bold")
        ax.set_title(label, fontsize=10)
        ax.axhline(0, color="black", linewidth=0.6)
        ax.tick_params(axis="x", labelsize=9)

    fig.suptitle("ETHUSDT — Run13 IS (Nov→May) vs Run15 OOS (May→Oct)",
                 fontsize=12, fontweight="bold", y=1.02)
    out = OUT / "eth_is_vs_oos.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out.relative_to(ROOT)}")


# ---------------------------------------------------------------------------
# Chart 3: ETH equity curves overlay (Run13 reconstructed + Run15)
# ---------------------------------------------------------------------------

def _equity_from_trades(trades: list[dict], initial: float = 10_000.0):
    rows = []
    for t in trades:
        ts_str = t.get("closed_ts") or t.get("exit_ts") or t.get("filled_ts")
        pnl = t.get("pnl_net") or t.get("pnl") or 0.0
        if ts_str is None:
            continue
        ts = datetime.fromisoformat(ts_str) if isinstance(ts_str, str) else ts_str
        rows.append((ts, pnl))
    rows.sort(key=lambda r: r[0])
    xs, ys = [rows[0][0] if rows else None], [initial]
    equity = initial
    for ts, pnl in rows:
        equity += pnl
        xs.append(ts)
        ys.append(equity)
    return xs, ys


def chart_eth_equity() -> None:
    is_run = _load("ETHUSDT_run13")
    oos_run = _load("ETHUSDT_run15")
    is_x, is_y = _equity_from_trades(is_run.get("trades", []))
    oos_x, oos_y = _equity_from_trades(oos_run.get("trades", []))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.2), sharey=False)

    for ax, (xs, ys, label, end_eq) in zip(
        (ax1, ax2),
        [(is_x, is_y, "Run13 IS (Nov→May)", is_y[-1] if is_y else 10_000),
         (oos_x, oos_y, "Run15 OOS (May→Oct)", oos_y[-1] if oos_y else 10_000)],
    ):
        color = POS if end_eq >= 10_000 else NEG
        ax.plot(xs, ys, color=color, linewidth=1.6)
        ax.fill_between(xs, 10_000, ys, color=color, alpha=0.12)
        ax.axhline(10_000, color=GREY, linewidth=0.8, linestyle="--", label="Initial $10k")
        ax.set_title(label)
        ax.set_ylabel("Equity ($)")
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v / 1000:.1f}k"))
        ax.xaxis.set_major_formatter(DateFormatter("%b %Y"))
        ax.tick_params(axis="x", rotation=0)
        delta = end_eq - 10_000
        ax.text(0.02, 0.95, f"Final: ${end_eq:,.0f}\n({delta:+,.0f} / {delta/100:+.1f}%)",
                transform=ax.transAxes, va="top", fontsize=10,
                bbox=dict(boxstyle="round,pad=0.4", facecolor="white",
                          edgecolor=color, alpha=0.9))
        ax.legend(loc="lower left", framealpha=0.95, fontsize=8)

    fig.suptitle("ETHUSDT equity curves — IS vs OOS (same config, ~11pp margin swing)",
                 fontsize=12, fontweight="bold", y=1.01)
    out = OUT / "eth_equity_curves.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    chart_per_symbol_margin()
    chart_eth_is_vs_oos()
    chart_eth_equity()
