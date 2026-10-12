"""Fetch and standardize A-share daily prices from AkShare."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

import pandas as pd


COLUMNS = [
    "symbol", "trade_date", "open", "high", "low", "close",
    "volume_lots", "amount_yuan",
]
SOURCE_COLUMNS = {
    "日期": "trade_date",
    "开盘": "open",
    "最高": "high",
    "最低": "low",
    "收盘": "close",
    "成交量": "volume_lots",
    "成交额": "amount_yuan",
}


def validate_symbol(symbol: str) -> str:
    """Accept a six-digit mainland A-share code, preserving leading zeroes."""
    if not re.fullmatch(r"[0-9]{6}", symbol):
        raise ValueError("股票代码必须是 6 位数字，例如 000001")
    return symbol


def normalize_daily_prices(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Convert AkShare's Chinese columns to stable names and data types."""
    validate_symbol(symbol)
    if raw is None or not isinstance(raw, pd.DataFrame):
        raise TypeError("行情数据必须是 pandas DataFrame")
    missing = set(SOURCE_COLUMNS) - set(raw.columns)
    if missing:
        raise ValueError(f"行情数据缺少字段: {', '.join(sorted(missing))}")

    prices = raw.rename(columns=SOURCE_COLUMNS)[list(SOURCE_COLUMNS.values())].copy()
    prices.insert(0, "symbol", symbol)
    prices["trade_date"] = pd.to_datetime(prices["trade_date"], errors="raise").dt.date
    for column in COLUMNS[2:]:
        prices[column] = pd.to_numeric(prices[column], errors="raise")
    if prices[COLUMNS[2:]].isna().any().any() or prices["trade_date"].isna().any():
        raise ValueError("行情数据包含空值")
    if (prices[["volume_lots", "amount_yuan"]] < 0).any().any():
        raise ValueError("成交量和成交额不能为负数")
    return (
        prices[COLUMNS]
        .drop_duplicates(subset=["symbol", "trade_date"], keep="last")
        .sort_values("trade_date")
        .reset_index(drop=True)
    )


def fetch_daily_prices(
    symbol: str,
    start_date: date,
    end_date: date,
    *,
    fetcher: Callable[..., pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Fetch unadjusted A-share daily prices; inject a fetcher for offline tests."""
    validate_symbol(symbol)
    if not isinstance(start_date, date) or not isinstance(end_date, date):
        raise TypeError("开始和结束日期必须是 date")
    if start_date > end_date:
        raise ValueError("开始日期不能晚于结束日期")
    if fetcher is None:
        import akshare as ak

        fetcher = ak.stock_zh_a_hist
    raw = fetcher(
        symbol=symbol,
        period="daily",
        start_date=start_date.strftime("%Y%m%d"),
        end_date=end_date.strftime("%Y%m%d"),
        adjust="",
    )
    return normalize_daily_prices(raw, symbol)
