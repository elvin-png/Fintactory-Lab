"""Point-in-time momentum and cross-sectional evaluation on daily prices."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd


def build_research_panel(
    prices: pd.DataFrame, trading_dates: Iterable[object] | None = None
) -> pd.DataFrame:
    """Calculate 20-session momentum and a separate, ex-post 5-session label.

    ``trading_dates`` should be a complete market calendar when available. Without
    one, the union of dates in ``prices`` is used. A missing or suspended session
    invalidates any calculation whose window includes it.
    """
    source_columns = ["symbol", "trade_date", "close", "volume_lots", "amount_yuan"]
    required = set(source_columns)
    if not isinstance(prices, pd.DataFrame):
        raise TypeError("prices must be a pandas DataFrame")
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"Missing price columns: {', '.join(sorted(missing))}")

    data = prices[source_columns].copy()
    if data["symbol"].isna().any() or (data["symbol"].astype(str).str.len() == 0).any():
        raise ValueError("symbol cannot be empty")
    data["trade_date"] = pd.to_datetime(data["trade_date"], errors="raise").dt.normalize()
    if data["trade_date"].isna().any():
        raise ValueError("trade_date cannot be empty")
    if data.duplicated(["symbol", "trade_date"]).any():
        raise ValueError("Duplicate symbol/trade_date rows")
    for column in ("close", "volume_lots", "amount_yuan"):
        data[column] = pd.to_numeric(data[column], errors="coerce")

    if trading_dates is None:
        dates = pd.DatetimeIndex(data["trade_date"].unique()).sort_values()
    else:
        dates = pd.DatetimeIndex(pd.to_datetime(list(trading_dates), errors="raise")).normalize()
        if dates.hasnans or dates.has_duplicates or not dates.is_monotonic_increasing:
            raise ValueError("trading_dates must be non-null, unique and sorted")
        if not data["trade_date"].isin(dates).all():
            raise ValueError("trading_dates must include every observed trade_date")

    columns = ["symbol", "trade_date", "momentum_20", "forward_return_5"]
    if data.empty:
        return pd.DataFrame(columns=columns)

    result = []
    for symbol, group in data.groupby("symbol", sort=True):
        series = group.set_index("trade_date").reindex(dates)
        close = series["close"].astype(float)
        valid = (
            np.isfinite(close) & (close > 0)
            & (series["volume_lots"] > 0)
            & (series["amount_yuan"] > 0)
        )
        # The current and all 20 preceding market sessions must be tradable.
        past_valid = valid.rolling(21, min_periods=21).sum().eq(21)
        momentum = (close / close.shift(20) - 1).where(past_valid)
        # The label includes the current and next five sessions; never feed it
        # back into the factor calculation.
        future_valid = valid.iloc[::-1].rolling(6, min_periods=6).sum().eq(6).iloc[::-1]
        forward = (close.shift(-5) / close - 1).where(future_valid)
        result.append(pd.DataFrame({
            "symbol": symbol,
            "trade_date": dates,
            "momentum_20": momentum.to_numpy(),
            "forward_return_5": forward.to_numpy(),
        }))
    return pd.concat(result, ignore_index=True)[columns]


def calculate_rank_ic(panel: pd.DataFrame, min_symbols: int = 3) -> pd.DataFrame:
    """Spearman correlation across symbols on each date, never across dates."""
    required = {"symbol", "trade_date", "momentum_20", "forward_return_5"}
    if not isinstance(panel, pd.DataFrame):
        raise TypeError("panel must be a pandas DataFrame")
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(f"Missing panel columns: {', '.join(sorted(missing))}")
    if min_symbols < 3:
        raise ValueError("min_symbols must be at least 3")
    if panel.duplicated(["symbol", "trade_date"]).any():
        raise ValueError("Duplicate symbol/trade_date rows")

    rows = []
    for trade_date, group in panel.groupby("trade_date", sort=True):
        pair = group[["momentum_20", "forward_return_5"]].replace(
            [np.inf, -np.inf], np.nan
        ).dropna()
        ic = np.nan
        if len(pair) >= min_symbols:
            ranks = pair.rank(method="average")
            if not ranks.nunique().eq(1).any():
                ic = ranks.iloc[:, 0].corr(ranks.iloc[:, 1], method="pearson")
        rows.append({"trade_date": trade_date, "n_symbols": len(pair), "rank_ic": ic})
    return pd.DataFrame(rows, columns=["trade_date", "n_symbols", "rank_ic"])
