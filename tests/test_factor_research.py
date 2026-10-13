import numpy as np
import pandas as pd
import pytest

from finfactor_lab.factor_research import (
    build_research_panel, build_research_panel_from_db, calculate_rank_ic,
)
from finfactor_lab.storage import save_daily_prices


@pytest.fixture
def sample_prices():
    dates = pd.bdate_range("2024-01-02", periods=31)
    rows = []
    for symbol, at_20, at_25 in [
        ("000001", 110.0, 111.1),
        ("000002", 120.0, 123.6),
        ("000003", 130.0, 132.6),
    ]:
        closes = np.r_[
            np.linspace(100, at_20, 21),
            np.linspace(at_20, at_25, 6)[1:],
            np.full(5, at_25),
        ]
        rows.extend({
            "symbol": symbol,
            "trade_date": day.date(),
            "close": close,
            "volume_lots": 1000.0,
            "amount_yuan": 100_000.0,
        } for day, close in zip(dates, closes))
    return pd.DataFrame(rows), dates


def test_momentum_and_future_label_are_aligned(sample_prices):
    prices, dates = sample_prices
    panel = build_research_panel(prices.sample(frac=1, random_state=4), dates)
    first = panel[panel.symbol == "000001"].reset_index(drop=True)

    assert first.loc[:19, "momentum_20"].isna().all()
    assert first.loc[20, "momentum_20"] == pytest.approx(0.10)
    assert first.loc[20, "forward_return_5"] == pytest.approx(0.01)
    assert first.loc[26:, "forward_return_5"].isna().all()
    assert first.loc[20, "trade_date"] == dates[20]

    ic = calculate_rank_ic(panel).set_index("trade_date")
    assert ic.loc[dates[20], "n_symbols"] == 3
    assert ic.loc[dates[20], "rank_ic"] == pytest.approx(0.5)
    assert pd.isna(ic.loc[dates[19], "rank_ic"])


def test_future_price_changes_do_not_change_past_factor(sample_prices):
    prices, dates = sample_prices
    original = build_research_panel(prices, dates)
    changed = prices.copy()
    changed.loc[changed.trade_date > dates[20].date(), "close"] *= 9
    revised = build_research_panel(changed, dates)

    before = original[original.trade_date <= dates[20]]["momentum_20"].reset_index(drop=True)
    after = revised[revised.trade_date <= dates[20]]["momentum_20"].reset_index(drop=True)
    pd.testing.assert_series_equal(before, after)
    assert original.loc[original.trade_date == dates[20], "forward_return_5"].tolist() != (
        revised.loc[revised.trade_date == dates[20], "forward_return_5"].tolist()
    )


def test_missing_and_suspended_sessions_invalidate_windows(sample_prices):
    prices, dates = sample_prices
    missing = prices[~((prices.symbol == "000001") & (prices.trade_date == dates[10].date()))]
    suspended = missing.copy()
    suspended.loc[
        (suspended.symbol == "000002") & (suspended.trade_date == dates[22].date()),
        "volume_lots",
    ] = 0
    suspended.loc[
        (suspended.symbol == "000003") & (suspended.trade_date == dates[22].date()),
        "close",
    ] = np.nan
    panel = build_research_panel(suspended, dates).set_index(["symbol", "trade_date"])

    assert pd.isna(panel.loc[("000001", dates[20]), "momentum_20"])
    assert pd.isna(panel.loc[("000001", dates[10]), "forward_return_5"])
    assert pd.isna(panel.loc[("000002", dates[20]), "forward_return_5"])
    assert pd.isna(panel.loc[("000003", dates[22]), "momentum_20"])
    assert pd.isna(panel.loc[("000003", dates[20]), "forward_return_5"])
    ic = calculate_rank_ic(panel.reset_index()).set_index("trade_date")
    assert ic.loc[dates[20], "n_symbols"] == 0
    assert pd.isna(ic.loc[dates[20], "rank_ic"])


def test_single_stock_is_not_a_cross_section(sample_prices):
    prices, dates = sample_prices
    panel = build_research_panel(prices[prices.symbol == "000001"], dates)
    ic = calculate_rank_ic(panel).set_index("trade_date")
    assert ic.loc[dates[20], "n_symbols"] == 1
    assert pd.isna(ic.loc[dates[20], "rank_ic"])


def test_rank_ic_is_computed_separately_for_each_date():
    panel = pd.DataFrame([
        {"symbol": symbol, "trade_date": day, "momentum_20": factor,
         "forward_return_5": future}
        for day, future_values in [
            ("2024-02-01", [1.0, 2.0, 3.0]),
            ("2024-02-02", [3.0, 2.0, 1.0]),
        ]
        for symbol, factor, future in zip(
            ["000001", "000002", "000003"], [1.0, 2.0, 3.0], future_values
        )
    ])
    ic = calculate_rank_ic(panel).set_index("trade_date")
    assert ic.loc["2024-02-01", "rank_ic"] == pytest.approx(1.0)
    assert ic.loc["2024-02-02", "rank_ic"] == pytest.approx(-1.0)
    assert ic["n_symbols"].tolist() == [3, 3]


def test_duplicate_rows_and_incomplete_calendar_are_rejected(sample_prices):
    prices, dates = sample_prices
    with pytest.raises(ValueError, match="Duplicate"):
        build_research_panel(pd.concat([prices, prices.iloc[[0]]]))
    with pytest.raises(ValueError, match="include every"):
        build_research_panel(prices, dates[:-1])
    panel = build_research_panel(prices, dates)
    with pytest.raises(ValueError, match="Duplicate"):
        calculate_rank_ic(pd.concat([panel, panel.iloc[[0]]]))


def test_rank_ic_from_multi_stock_duckdb(sample_prices, tmp_path):
    prices, dates = sample_prices
    stored = prices.copy()
    stored["open"] = stored["close"]
    stored["high"] = stored["close"]
    stored["low"] = stored["close"]
    db = tmp_path / "research.duckdb"
    save_daily_prices(stored, db)
    panel = build_research_panel_from_db(
        ["000001", "000002", "000003"],
        dates[0].date(), dates[-1].date(), db, dates,
    )
    ic = calculate_rank_ic(panel).set_index("trade_date")
    assert ic.loc[dates[20], "n_symbols"] == 3
    assert ic.loc[dates[20], "rank_ic"] == pytest.approx(0.5)
