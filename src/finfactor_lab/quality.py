"""Observable quality checks for normalized daily prices."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from finfactor_lab.market_data import COLUMNS


@dataclass(frozen=True)
class QualityReport:
    rows: int
    missing_values: int
    duplicate_keys: int
    abnormal_prices: int
    negative_turnover: int
    extreme_returns: int

    @property
    def clean(self) -> bool:
        return not any((self.missing_values, self.duplicate_keys,
                        self.abnormal_prices, self.negative_turnover))


def check_daily_prices(prices: pd.DataFrame) -> QualityReport:
    """Count missing fields, duplicate keys and invalid OHLC values.

    Abnormal prices include nonfinite/nonpositive OHLC and bars inconsistent
    with their high/low. These are counts of rows, not a claim about returns:
    large unadjusted moves can be legitimate after corporate actions and are
    reported separately without rejecting the rows.
    """
    if not isinstance(prices, pd.DataFrame):
        raise TypeError("行情数据必须是 pandas DataFrame")
    missing = set(COLUMNS) - set(prices.columns)
    if missing:
        raise ValueError(f"标准化行情缺少字段: {', '.join(sorted(missing))}")
    data = prices[COLUMNS]
    missing_values = int(data.isna().sum().sum())
    duplicate_keys = int(data.duplicated(["symbol", "trade_date"]).sum())
    ohlc = data[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
    abnormal = (
        ~np.isfinite(ohlc).all(axis=1)
        | (ohlc <= 0).any(axis=1)
        | (ohlc["high"] < ohlc[["open", "low", "close"]].max(axis=1))
        | (ohlc["low"] > ohlc[["open", "high", "close"]].min(axis=1))
    )
    turnover = data[["volume_lots", "amount_yuan"]].apply(
        pd.to_numeric, errors="coerce"
    )
    negative_turnover = int((turnover < 0).any(axis=1).sum())
    ordered = data.assign(_close=ohlc["close"]).sort_values(["symbol", "trade_date"])
    returns = ordered.groupby("symbol", sort=False)["_close"].pct_change(fill_method=None)
    extreme_returns = int((np.isfinite(returns) & (returns.abs() > 0.5)).sum())
    return QualityReport(len(data), missing_values, duplicate_keys,
                         int(abnormal.sum()), negative_turnover, extreme_returns)
