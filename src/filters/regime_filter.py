"""Regime-based pre-dispatch filter.

Thin wrapper around `analysis.trend_classifier.classify_htf_trend`. Decides
whether a signal's HTF regime is currently tradeable under user-configurable
include/exclude flags.

Returned regime label is one of:
    'bullish' | 'bearish' | 'ranging' | 'unknown'
    ('unknown' = classifier returned None because there weren't enough bars)
"""

from __future__ import annotations

import polars as pl

from src.analysis.trend_classifier import classify_htf_trend


def is_regime_tradeable(
    htf_bars: pl.DataFrame,
    exclude_ranging: bool = True,
    exclude_trending: bool = False,
    classification_lookback: int = 20,
) -> tuple[bool, str]:
    """Return `(tradeable, regime_label)`.

    `htf_bars` must already be on the regime timeframe (e.g. 4h-aggregated).
    The classifier looks at the last `classification_lookback` bars.

    Defaults (`exclude_ranging=True`, `exclude_trending=False`) match the
    multi_run1 finding that ranging regimes produce 77% of total loss while
    trending regimes are roughly break-even.

    `unknown` is treated as tradeable — better to take a signal under
    uncertainty than to suppress every trade during the warm-up window when
    only a few HTF bars exist yet.
    """
    label = classify_htf_trend(htf_bars, lookback=classification_lookback)
    if label is None:
        return (True, "unknown")

    if label == "ranging" and exclude_ranging:
        return (False, "ranging")
    if label in ("bullish", "bearish") and exclude_trending:
        return (False, label)
    return (True, label)
