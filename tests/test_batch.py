from datetime import date

import pandas as pd

from finfactor_lab.batch import update_many
from finfactor_lab.market_data import normalize_daily_prices
from finfactor_lab.quality import check_daily_prices
from finfactor_lab.storage import load_many_daily_prices


def raw(day: str, close: float = 10.0) -> pd.DataFrame:
    return pd.DataFrame({
        "日期": [day], "开盘": [close], "最高": [close + 1],
        "最低": [close - 1], "收盘": [close], "成交量": [100],
        "成交额": [100000],
    })


def test_batch_retry_isolation_and_incremental_upsert(tmp_path):
    db = tmp_path / "batch.duckdb"
    calls = []

    def fetcher(**kwargs):
        calls.append(kwargs)
        symbol = kwargs["symbol"]
        attempts = sum(call["symbol"] == symbol for call in calls)
        if symbol == "000002":
            raise ConnectionError("source unavailable")
        if symbol == "000001" and attempts == 1:
            raise TimeoutError("transient")
        return raw("2024-01-10", close=10 + attempts)

    results = update_many(
        ["000001", "000002", "000003", "000001"],
        date(2024, 1, 1), date(2024, 1, 15), db,
        retries=1, fetcher=fetcher, sleep=lambda _: None,
    )
    assert [(r.symbol, r.status, r.attempts) for r in results] == [
        ("000001", "saved", 2), ("000002", "failed", 2),
        ("000003", "saved", 1),
    ]
    assert "ConnectionError" in results[1].error
    assert {c["adjust"] for c in calls} == {""}

    calls.clear()
    second = update_many(
        ["000001", "000002", "000003"],
        date(2024, 1, 1), date(2024, 1, 15), db,
        retries=0, fetcher=fetcher, sleep=lambda _: None,
    )
    assert second[0].start_date == date(2024, 1, 3)
    assert second[1].start_date == date(2024, 1, 1)
    refresh = update_many(
        ["000001"], date(2024, 1, 1), date(2024, 1, 15), db,
        retries=0, full_refresh=True, fetcher=fetcher,
    )
    assert refresh[0].start_date == date(2024, 1, 1)
    stored = load_many_daily_prices(
        ["000001", "000002", "000003"],
        date(2024, 1, 1), date(2024, 1, 15), db,
    )
    assert len(stored) == 2
    assert stored["symbol"].tolist() == ["000001", "000003"]
    assert not stored.duplicated(["symbol", "trade_date"]).any()


def test_quality_report_counts_broken_rows():
    data = normalize_daily_prices(raw("2024-01-10"), "000001")
    broken = pd.concat([data, data], ignore_index=True)
    broken.loc[1, "high"] = 0.5
    broken.loc[1, "amount_yuan"] = None
    report = check_daily_prices(broken)
    assert report.rows == 2
    assert report.missing_values == 1
    assert report.duplicate_keys == 1
    assert report.abnormal_prices == 1
    assert not report.clean


def test_extreme_unadjusted_move_is_reported_not_rejected():
    first = normalize_daily_prices(raw("2024-01-10", 10), "000001")
    second = normalize_daily_prices(raw("2024-01-11", 20), "000001")
    report = check_daily_prices(pd.concat([first, second], ignore_index=True))
    assert report.extreme_returns == 1
    assert report.clean


def test_bad_symbol_is_rejected_before_network(tmp_path):
    try:
        update_many(["000001", "bad"], date(2024, 1, 1), date(2024, 1, 2),
                    tmp_path / "bad.duckdb", fetcher=lambda **_: None)
    except ValueError as exc:
        assert "6 位数字" in str(exc)
    else:
        raise AssertionError("invalid symbol accepted")
    assert not (tmp_path / "bad.duckdb").exists()


def test_25_symbols_have_unique_typed_rows(tmp_path):
    symbols = [f"{number:06d}" for number in range(1, 26)]
    db = tmp_path / "universe.duckdb"
    results = update_many(
        symbols, date(2024, 1, 1), date(2024, 1, 31), db,
        retries=0, fetcher=lambda **_: raw("2024-01-10"),
    )
    stored = load_many_daily_prices(symbols, date(2024, 1, 1), date(2024, 1, 31), db)
    assert len(results) == len(stored) == 25
    assert all(result.status == "saved" for result in results)
    assert stored["symbol"].nunique() == 25
    assert str(stored["trade_date"].dtype).startswith("datetime64")
    assert stored["close"].dtype == "float64"
    assert check_daily_prices(stored).clean
