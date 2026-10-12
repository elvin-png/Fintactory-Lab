from datetime import date

import pandas as pd
import pytest

from finfactor_lab.market_data import fetch_daily_prices, normalize_daily_prices


@pytest.fixture
def sample_raw() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "日期": ["2024-01-03", "2024-01-02"],
            "股票代码": ["000001", "000001"],
            "开盘": ["10.2", "10.0"],
            "收盘": ["10.3", "10.1"],
            "最高": ["10.4", "10.2"],
            "最低": ["10.1", "9.9"],
            "成交量": [1000, 900],
            "成交额": [1030000, 909000],
        }
    )


def test_fetch_uses_daily_unadjusted_api_and_normalizes(sample_raw):
    calls = []

    def fake_fetcher(**kwargs):
        calls.append(kwargs)
        return sample_raw

    result = fetch_daily_prices(
        "000001", date(2024, 1, 2), date(2024, 1, 3), fetcher=fake_fetcher
    )

    assert calls == [{
        "symbol": "000001", "period": "daily", "start_date": "20240102",
        "end_date": "20240103", "adjust": "",
    }]
    assert result["trade_date"].tolist() == [date(2024, 1, 2), date(2024, 1, 3)]
    assert result["symbol"].tolist() == ["000001", "000001"]
    assert result["close"].tolist() == [10.1, 10.3]


def test_invalid_input_is_rejected_before_fetch(sample_raw):
    def fake_fetcher(**kwargs):
        raise AssertionError("fetcher should not be called")

    with pytest.raises(ValueError, match="6 位数字"):
        fetch_daily_prices("1", date(2024, 1, 1), date(2024, 1, 2), fetcher=fake_fetcher)
    with pytest.raises(ValueError, match="开始日期"):
        fetch_daily_prices(
            "000001", date(2024, 1, 3), date(2024, 1, 2), fetcher=fake_fetcher
        )
    with pytest.raises(ValueError, match="缺少字段"):
        normalize_daily_prices(sample_raw.drop(columns="收盘"), "000001")
