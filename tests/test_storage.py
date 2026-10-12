from datetime import date

import pandas as pd

from finfactor_lab.market_data import normalize_daily_prices
from finfactor_lab.storage import load_daily_prices, save_daily_prices


def test_upsert_and_reload_from_file(tmp_path):
    db_path = tmp_path / "nested" / "quotes.duckdb"
    raw = pd.DataFrame({
        "日期": ["2024-01-02"], "开盘": [10.0], "最高": [10.5],
        "最低": [9.9], "收盘": [10.2], "成交量": [1000], "成交额": [1020000],
    })
    first = normalize_daily_prices(raw, "000001")
    assert save_daily_prices(first, db_path) == 1

    updated = first.copy()
    updated.loc[0, "close"] = 10.3
    assert save_daily_prices(updated, db_path) == 1

    loaded = load_daily_prices(
        "000001", date(2024, 1, 1), date(2024, 1, 3), db_path
    )
    assert db_path.exists()
    assert len(loaded) == 1
    assert loaded.iloc[0]["close"] == 10.3
    assert loaded.iloc[0]["symbol"] == "000001"


def test_empty_data_creates_database_without_rows(tmp_path):
    db_path = tmp_path / "empty.duckdb"
    empty = normalize_daily_prices(pd.DataFrame(columns=[
        "日期", "开盘", "最高", "最低", "收盘", "成交量", "成交额"
    ]), "000001")
    assert save_daily_prices(empty, db_path) == 0
    assert load_daily_prices(
        "000001", date(2024, 1, 1), date(2024, 1, 31), db_path
    ).empty
