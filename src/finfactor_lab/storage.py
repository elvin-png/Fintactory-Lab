"""Persist normalized daily prices in a local DuckDB file."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from finfactor_lab.market_data import COLUMNS, validate_symbol


DEFAULT_DB_PATH = Path("data/market_data.duckdb")


def _connect(db_path: str | Path) -> duckdb.DuckDBPyConnection:
    path = Path(db_path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(path))
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_prices (
            symbol VARCHAR NOT NULL,
            trade_date DATE NOT NULL,
            open DOUBLE NOT NULL,
            high DOUBLE NOT NULL,
            low DOUBLE NOT NULL,
            close DOUBLE NOT NULL,
            volume_lots DOUBLE NOT NULL,
            amount_yuan DOUBLE NOT NULL,
            PRIMARY KEY (symbol, trade_date)
        )
        """
    )
    return connection


def save_daily_prices(
    prices: pd.DataFrame, db_path: str | Path = DEFAULT_DB_PATH
) -> int:
    """Upsert normalized rows, so repeated fetches do not duplicate days."""
    if not isinstance(prices, pd.DataFrame):
        raise TypeError("行情数据必须是 pandas DataFrame")
    missing = set(COLUMNS) - set(prices.columns)
    if missing:
        raise ValueError(f"标准化行情缺少字段: {', '.join(sorted(missing))}")
    with _connect(db_path) as connection:
        if prices.empty:
            return 0
        connection.register("incoming_prices", prices[COLUMNS])
        try:
            connection.execute(
                """
                INSERT INTO daily_prices BY NAME
                SELECT * FROM incoming_prices
                ON CONFLICT (symbol, trade_date) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume_lots = excluded.volume_lots,
                    amount_yuan = excluded.amount_yuan
                """
            )
        finally:
            connection.unregister("incoming_prices")
    return len(prices)


def load_daily_prices(
    symbol: str,
    start_date: date,
    end_date: date,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> pd.DataFrame:
    """Read stored daily prices in date order."""
    validate_symbol(symbol)
    if start_date > end_date:
        raise ValueError("开始日期不能晚于结束日期")
    with _connect(db_path) as connection:
        return connection.execute(
            """
            SELECT symbol, trade_date, open, high, low, close,
                   volume_lots, amount_yuan
            FROM daily_prices
            WHERE symbol = ? AND trade_date BETWEEN ? AND ?
            ORDER BY trade_date
            """,
            [symbol, start_date, end_date],
        ).fetchdf()


def latest_trade_date(
    symbol: str, db_path: str | Path = DEFAULT_DB_PATH
) -> date | None:
    """Return the latest stored session for a symbol, if any."""
    validate_symbol(symbol)
    with _connect(db_path) as connection:
        return connection.execute(
            "SELECT MAX(trade_date) FROM daily_prices WHERE symbol = ?", [symbol]
        ).fetchone()[0]


def load_many_daily_prices(
    symbols: list[str],
    start_date: date,
    end_date: date,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> pd.DataFrame:
    """Read a multi-stock panel in one query, ordered by session and symbol."""
    if start_date > end_date:
        raise ValueError("开始日期不能晚于结束日期")
    symbols = list(dict.fromkeys(validate_symbol(symbol) for symbol in symbols))
    with _connect(db_path) as connection:
        if not symbols:
            return connection.execute(
                "SELECT * FROM daily_prices WHERE FALSE"
            ).fetchdf()
        placeholders = ", ".join("?" for _ in symbols)
        return connection.execute(
            f"""SELECT {', '.join(COLUMNS)} FROM daily_prices
                WHERE symbol IN ({placeholders}) AND trade_date BETWEEN ? AND ?
                ORDER BY trade_date, symbol""",
            [*symbols, start_date, end_date],
        ).fetchdf()
