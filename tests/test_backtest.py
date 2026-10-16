from datetime import date

import pandas as pd
import pytest

from finfactor_lab.backtest import (
    BacktestConfig, BacktestEngine, Broker, DuckDBPriceSource, Strategy,
    TopNMomentumStrategy,
)
from finfactor_lab.storage import save_daily_prices


class FixedStrategy(Strategy):
    def __init__(self, weights):
        self.weights = weights
        self.calls = []

    def target_weights(self, history, as_of, trading_dates):
        self.calls.append((as_of, history.trade_date.max()))
        return self.weights


def prices(dates, symbols=("000001",), opens=None, closes=None, volumes=None):
    rows = []
    for index, day in enumerate(dates):
        for symbol in symbols:
            opening = (opens or {}).get((index, symbol), 10.0)
            closing = (closes or {}).get((index, symbol), opening)
            rows.append({
                "symbol": symbol, "trade_date": day, "open": opening,
                "high": max(opening, closing), "low": min(opening, closing),
                "close": closing,
                "volume_lots": (volumes or {}).get((index, symbol), 100.0),
                "amount_yuan": 10_000.0,
            })
    return pd.DataFrame(rows)


def run_db(tmp_path, data, strategy, *, trading_dates=None,
           open_tradability=None, **config_kwargs):
    db = tmp_path / "backtest.duckdb"
    save_daily_prices(data, db)
    days = sorted(data.trade_date.unique())
    config = BacktestConfig(tuple(sorted(data.symbol.unique())), days[0], days[-1],
                            **config_kwargs)
    return BacktestEngine(config, strategy, DuckDBPriceSource(db)).run(
        trading_dates=trading_dates, open_tradability=open_tradability
    )


def test_signal_uses_close_and_trades_next_session(tmp_path):
    days = [date(2024, 1, 30), date(2024, 1, 31), date(2024, 2, 1)]
    strategy = FixedStrategy({"000001": 1.0})
    result = run_db(tmp_path, prices(days), strategy, trading_dates=days,
                    initial_cash=1_000,
                    commission_rate=0, lot_size=1)

    assert strategy.calls == [(pd.Timestamp("2024-01-31"), pd.Timestamp("2024-01-31"))]
    assert result.trades.trade_date.tolist() == [pd.Timestamp("2024-02-01")]
    assert result.equity_curve.equity.tolist() == [1_000, 1_000, 1_000]


def test_future_price_change_does_not_change_earlier_momentum_trade(tmp_path):
    days = pd.bdate_range("2024-01-02", periods=24).date
    symbols = ("000001", "000002")
    closes = {(i, symbol): (10 + i if symbol == "000001" else 10 + i / 2)
              for i in range(24) for symbol in symbols}
    original = prices(days, symbols, closes=closes)
    changed = original.copy()
    changed.loc[(changed.trade_date >= days[22]) &
                (changed.symbol == "000002"), "close"] *= 20
    changed["high"] = changed[["open", "close"]].max(axis=1)
    config = dict(initial_cash=10_000, commission_rate=0,
                  lot_size=1, rebalance_frequency="daily")
    first = run_db(tmp_path / "first", original, TopNMomentumStrategy(1), **config)
    second = run_db(tmp_path / "second", changed, TopNMomentumStrategy(1), **config)

    first_trade_day = pd.Timestamp(days[21])
    first_trade = first.trades[first.trades.trade_date == first_trade_day]
    second_trade = second.trades[second.trades.trade_date == first_trade_day]
    pd.testing.assert_frame_equal(first_trade.reset_index(drop=True),
                                  second_trade.reset_index(drop=True))
    assert first_trade.symbol.tolist() == ["000001"]


def test_commission_reduces_cash_and_equity(tmp_path):
    days = [date(2024, 1, 31), date(2024, 2, 1)]
    result = run_db(tmp_path, prices(days), FixedStrategy({"000001": 1.0}),
                    trading_dates=days,
                    initial_cash=1_000, commission_rate=0.01, lot_size=1)

    trade = result.trades.iloc[0]
    assert trade.quantity == 99
    assert trade.fee == pytest.approx(9.9)
    assert result.equity_curve.iloc[-1].cash == pytest.approx(0.1)
    assert result.equity_curve.iloc[-1].equity == pytest.approx(990.1)


def test_suspension_prevents_execution(tmp_path):
    days = [date(2024, 1, 31), date(2024, 2, 1)]
    data = prices(days, volumes={(1, "000001"): 0})
    opening_status = pd.DataFrame({
        "symbol": ["000001", "000001"], "trade_date": days,
        "tradable_at_open": [True, False],
    })
    result = run_db(tmp_path, data, FixedStrategy({"000001": 1.0}),
                    trading_dates=days, open_tradability=opening_status,
                    initial_cash=1_000, commission_rate=0)

    assert result.trades.empty
    assert result.orders.status.tolist() == ["not_tradable_at_open"]
    assert result.equity_curve.cash.tolist() == [1_000, 1_000]


def test_cash_holdings_and_benchmark_equity_curve(tmp_path):
    days = [date(2024, 1, 31), date(2024, 2, 1)]
    symbols = ("000001", "000002")
    data = prices(days, symbols,
                  opens={(0, "000002"): 20, (1, "000002"): 20},
                  closes={(0, "000002"): 20, (1, "000001"): 12,
                          (1, "000002"): 18})
    db = tmp_path / "portfolio.duckdb"
    save_daily_prices(data, db)
    config = BacktestConfig(symbols, days[0], days[-1], initial_cash=1_000,
                            commission_rate=0, lot_size=1)
    benchmark = pd.DataFrame({"trade_date": days, "close": [100, 110]})
    result = BacktestEngine(config, FixedStrategy({"000001": 0.5, "000002": 0.5}),
                            DuckDBPriceSource(db)).run(benchmark, trading_dates=days)

    assert dict(zip(result.positions.symbol, result.positions.shares)) == {
        "000001": 50, "000002": 25,
    }
    last = result.equity_curve.iloc[-1]
    assert last.cash == 0
    assert last.market_value == 1_050
    assert last.equity == 1_050
    assert last.daily_return == pytest.approx(0.05)
    assert last.benchmark_return == pytest.approx(0.1)
    assert last.benchmark_equity == pytest.approx(1_100)


def test_explicit_calendar_keeps_fully_missing_sessions_in_momentum_window(tmp_path):
    days = list(pd.bdate_range("2024-01-02", periods=5).date)
    data = prices([days[0], *days[2:]])
    db = tmp_path / "calendar.duckdb"
    save_daily_prices(data, db)
    config = BacktestConfig(("000001",), days[0], days[-1],
                            rebalance_frequency="daily", commission_rate=0)
    result = BacktestEngine(config, TopNMomentumStrategy(1, lookback=2),
                            DuckDBPriceSource(db)).run(trading_dates=pd.DatetimeIndex(days))

    assert result.trades.empty
    assert result.equity_curve.trade_date.tolist() == list(pd.DatetimeIndex(days))


def test_open_execution_does_not_read_future_close_or_daily_turnover():
    day = pd.Timestamp("2024-02-01")

    class OpeningOnly(dict):
        def __getitem__(self, key):
            if key in {"close", "volume_lots", "amount_yuan"}:
                raise AssertionError(f"future field read at open: {key}")
            return super().__getitem__(key)

    assert Broker._tradable(OpeningOnly(open=10.0))

    def execute(close, volume, amount):
        broker = Broker(1_000, 0, 1)
        bar = pd.DataFrame([{
            "symbol": "000001", "open": 10.0, "close": close,
            "volume_lots": volume, "amount_yuan": amount,
        }])
        broker.execute(day, bar, {"000001": 1.0})
        return broker.trades, broker.holdings

    assert execute(10.0, 100, 10_000) == execute(0.0, 0, 0)


def test_equal_weight_buys_reserve_fees_and_avoid_symbol_order_bias():
    day = pd.Timestamp("2024-02-01")
    bars = pd.DataFrame([
        {"symbol": symbol, "open": 10.0, "close": 10.0}
        for symbol in ("000001", "000002")
    ])
    targets = {"000001": 0.5, "000002": 0.5}

    funded = Broker(2_020, 0.001, 100)
    funded.execute(day, bars, targets)
    assert funded.holdings == {"000001": 100, "000002": 100}
    assert funded.cash == pytest.approx(18)

    constrained = Broker(2_000, 0.001, 100)
    constrained.execute(day, bars, targets)
    assert constrained.holdings == {}
    assert sorted(constrained.orders[i]["symbol"] for i in range(2)) == [
        "000001", "000002",
    ]
    assert {order["status"] for order in constrained.orders} == {
        "insufficient_cash",
    }


def test_monthly_rebalance_dates_do_not_follow_stock_coverage(tmp_path):
    days = [date(2024, 1, 30), date(2024, 1, 31), date(2024, 2, 1)]
    sparse = prices([days[0], days[2]])
    complete = prices(days)
    first_strategy = FixedStrategy({"000001": 1.0})
    second_strategy = FixedStrategy({"000001": 1.0})
    first = run_db(tmp_path / "sparse", sparse, first_strategy,
                   trading_dates=days, initial_cash=1_000,
                   commission_rate=0, lot_size=1)
    second = run_db(tmp_path / "complete", complete, second_strategy,
                    trading_dates=days, initial_cash=1_000,
                    commission_rate=0, lot_size=1)

    assert [call[0] for call in first_strategy.calls] == [pd.Timestamp(days[1])]
    assert [call[0] for call in second_strategy.calls] == [pd.Timestamp(days[1])]
    assert first.trades.trade_date.tolist() == second.trades.trade_date.tolist()
    assert first.calendar_source == second.calendar_source == "provided"


def test_monthly_requires_independent_calendar_and_daily_marks_fallback(tmp_path):
    days = [date(2024, 1, 31), date(2024, 2, 1)]
    with pytest.raises(ValueError, match="requires an independent trading_dates"):
        run_db(tmp_path / "monthly", prices(days), FixedStrategy({"000001": 1.0}))
    daily = run_db(tmp_path / "daily", prices(days),
                   FixedStrategy({"000001": 1.0}), rebalance_frequency="daily")
    assert daily.calendar_source == "observed_prices"
    assert "omit market sessions" in daily.calendar_warning


def test_suspended_position_uses_reported_close_then_marks_carried_price(tmp_path):
    days = list(pd.bdate_range("2024-01-31", periods=4).date)
    data = prices(days[:3], closes={(1, "000001"): 12, (2, "000001"): 13},
                  volumes={(2, "000001"): 0})
    db = tmp_path / "valuation.duckdb"
    save_daily_prices(data, db)
    opening_status = pd.DataFrame({
        "symbol": ["000001"] * 3, "trade_date": days[:3],
        "tradable_at_open": [True, True, False],
    })
    config = BacktestConfig(("000001",), days[0], days[-1],
                            initial_cash=1_000, commission_rate=0, lot_size=1)
    result = BacktestEngine(config, FixedStrategy({"000001": 1.0}),
                            DuckDBPriceSource(db)).run(
        trading_dates=days, open_tradability=opening_status
    )

    positions = result.positions.set_index("trade_date")
    assert positions.loc[pd.Timestamp(days[2]), "valuation_status"] == "reported_close"
    assert positions.loc[pd.Timestamp(days[2]), "valuation_price"] == 13
    assert positions.loc[pd.Timestamp(days[3]), "valuation_status"] == "carried_forward"
    assert positions.loc[pd.Timestamp(days[3]), "valuation_price"] == 13
    assert result.equity_curve.carried_positions.tolist() == [0, 0, 0, 1]
    assert result.equity_curve.equity.tolist() == [1_000, 1_200, 1_300, 1_300]
