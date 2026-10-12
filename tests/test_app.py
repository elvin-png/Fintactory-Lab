"""Exercise the page without calling AkShare or writing a real database."""

from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

from finfactor_lab import market_data, storage


APP_PATH = Path(__file__).resolve().parents[1] / "src" / "finfactor_lab" / "app.py"


def test_fetch_button_saves_and_displays_mock_prices(monkeypatch):
    prices = pd.DataFrame({
        "symbol": ["000001"],
        "trade_date": [pd.Timestamp("2024-01-02").date()],
        "open": [10.0],
        "high": [10.5],
        "low": [9.9],
        "close": [10.2],
        "volume_lots": [1000.0],
        "amount_yuan": [1020000.0],
    })
    calls = []
    saved = False

    def fake_fetch(symbol, start_date, end_date):
        calls.append(symbol)
        return prices

    def fake_save(frame):
        nonlocal saved
        assert frame.equals(prices)
        saved = True
        return len(frame)

    def fake_load(symbol, start_date, end_date):
        return prices if saved else prices.iloc[0:0]

    monkeypatch.setattr(market_data, "fetch_daily_prices", fake_fetch)
    monkeypatch.setattr(storage, "save_daily_prices", fake_save)
    monkeypatch.setattr(storage, "load_daily_prices", fake_load)

    page = AppTest.from_file(str(APP_PATH)).run(timeout=10)
    assert not page.exception
    assert len(page.info) == 1

    page.button[0].click().run(timeout=10)
    assert not page.exception
    assert calls == ["000001"]
    assert saved
    assert len(page.success) == 1
    assert len(page.dataframe) == 1
