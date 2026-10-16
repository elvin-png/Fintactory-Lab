"""Daily, long-only backtesting on the existing normalized price table."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from finfactor_lab.market_data import validate_symbol
from finfactor_lab.storage import DEFAULT_DB_PATH, load_many_daily_prices


@dataclass(frozen=True)
class BacktestConfig:
    symbols: tuple[str, ...]
    start_date: date
    end_date: date
    initial_cash: float = 1_000_000.0
    rebalance_frequency: Literal["daily", "weekly", "monthly"] = "monthly"
    commission_rate: float = 0.001
    lot_size: int = 100

    def __post_init__(self) -> None:
        symbols = tuple(dict.fromkeys(validate_symbol(s) for s in self.symbols))
        if not symbols or self.start_date > self.end_date:
            raise ValueError("symbols must be nonempty and start_date <= end_date")
        if not np.isfinite(self.initial_cash) or self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive and finite")
        if self.rebalance_frequency not in ("daily", "weekly", "monthly"):
            raise ValueError("rebalance_frequency must be daily, weekly or monthly")
        if not np.isfinite(self.commission_rate) or not 0 <= self.commission_rate < 1:
            raise ValueError("commission_rate must be finite and between zero and one")
        if not isinstance(self.lot_size, int) or self.lot_size <= 0:
            raise ValueError("lot_size must be a positive integer")
        object.__setattr__(self, "symbols", symbols)


class DuckDBPriceSource:
    """Read all available history through the end date for signal warmup."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path

    def load(self, config: BacktestConfig) -> pd.DataFrame:
        return load_many_daily_prices(
            list(config.symbols), date.min, config.end_date, self.db_path
        )


class Strategy(ABC):
    @abstractmethod
    def target_weights(
        self, history: pd.DataFrame, as_of: pd.Timestamp,
        trading_dates: pd.DatetimeIndex,
    ) -> dict[str, float]:
        """Return long-only target weights using history no later than as_of."""


class TopNMomentumStrategy(Strategy):
    def __init__(self, top_n: int = 5, lookback: int = 20) -> None:
        if top_n <= 0 or lookback <= 0:
            raise ValueError("top_n and lookback must be positive")
        self.top_n = top_n
        self.lookback = lookback

    def target_weights(
        self, history: pd.DataFrame, as_of: pd.Timestamp,
        trading_dates: pd.DatetimeIndex,
    ) -> dict[str, float]:
        dates = trading_dates[trading_dates <= as_of]
        if len(dates) <= self.lookback:
            return {}
        scores: list[tuple[str, float]] = []
        for symbol, group in history.groupby("symbol", sort=True):
            recent = group.set_index("trade_date").reindex(dates[-self.lookback - 1:])
            close = pd.to_numeric(recent["close"], errors="coerce")
            volume = pd.to_numeric(recent["volume_lots"], errors="coerce")
            amount = pd.to_numeric(recent["amount_yuan"], errors="coerce")
            valid = np.isfinite(close) & (close > 0) & (volume > 0) & (amount > 0)
            if valid.all():
                scores.append((symbol, float(close.iloc[-1] / close.iloc[0] - 1)))
        winners = sorted(scores, key=lambda item: (-item[1], item[0]))[:self.top_n]
        return {symbol: 1.0 / len(winners) for symbol, _ in winners}


@dataclass
class BacktestResult:
    equity_curve: pd.DataFrame
    trades: pd.DataFrame
    orders: pd.DataFrame
    positions: pd.DataFrame
    calendar_source: Literal["provided", "observed_prices"]
    calendar_warning: str | None


class Broker:
    """Apply target weights at the next open and value holdings at the close."""

    def __init__(self, initial_cash: float, commission_rate: float, lot_size: int) -> None:
        if not np.isfinite(commission_rate) or not 0 <= commission_rate < 1:
            raise ValueError("commission_rate must be finite and between zero and one")
        self.cash = float(initial_cash)
        self.commission_rate = commission_rate
        self.lot_size = lot_size
        self.holdings: dict[str, int] = {}
        self.last_close: dict[str, float] = {}
        self.trades: list[dict] = []
        self.orders: list[dict] = []

    @staticmethod
    def _tradable(bar: pd.Series | None) -> bool:
        # Close and full-session turnover are unavailable when the open order
        # is sent. A caller may supply an independently known opening status.
        return bool(
            bar is not None and np.isfinite(bar["open"]) and bar["open"] > 0
            and bar.get("tradable_at_open", True)
        )

    def execute(
        self, trade_date: pd.Timestamp, bars: pd.DataFrame,
        weights: dict[str, float],
    ) -> None:
        by_symbol = {row["symbol"]: row for _, row in bars.iterrows()}
        equity_at_open = self.cash + sum(
            shares * (
                float(by_symbol[symbol]["open"])
                if self._tradable(by_symbol.get(symbol)) else self.last_close[symbol]
            )
            for symbol, shares in self.holdings.items()
        )
        targets: dict[str, int] = {}
        for symbol in set(weights) | set(self.holdings):
            bar = by_symbol.get(symbol)
            if not self._tradable(bar):
                if weights.get(symbol, 0) != 0 or self.holdings.get(symbol, 0) != 0:
                    self.orders.append({"trade_date": trade_date, "symbol": symbol,
                                        "status": "not_tradable_at_open"})
                continue
            targets[symbol] = int(
                (equity_at_open * weights.get(symbol, 0) / float(bar["open"]))
                // self.lot_size
            ) * self.lot_size

        # Sell first so proceeds can fund buys. Existing holdings are never sold
        # on a day without a tradable bar.
        for symbol in sorted(targets):
            delta = targets[symbol] - self.holdings.get(symbol, 0)
            if delta < 0:
                self._fill(trade_date, symbol, delta, float(by_symbol[symbol]["open"]))
        buys = {symbol: targets[symbol] - self.holdings.get(symbol, 0)
                for symbol in targets
                if targets[symbol] > self.holdings.get(symbol, 0)}
        total_cost = sum(
            quantity * float(by_symbol[symbol]["open"]) * (1 + self.commission_rate)
            for symbol, quantity in buys.items()
        )
        # Reserve every buy fee before allocating cash. When the complete
        # basket is unaffordable, scale all requests equally before lot rounding.
        scale = min(1.0, self.cash / total_cost) if total_cost else 1.0
        for symbol in sorted(buys):
            quantity = int(buys[symbol] * scale // self.lot_size) * self.lot_size
            if quantity:
                self._fill(trade_date, symbol, quantity,
                           float(by_symbol[symbol]["open"]))
            if quantity < buys[symbol]:
                self.orders.append({"trade_date": trade_date, "symbol": symbol,
                                    "status": "insufficient_cash"})

    def _fill(self, trade_date: pd.Timestamp, symbol: str,
              quantity: int, price: float) -> None:
        gross = abs(quantity) * price
        fee = gross * self.commission_rate
        self.cash -= quantity * price + fee
        self.holdings[symbol] = self.holdings.get(symbol, 0) + quantity
        if quantity > 0:
            self.last_close[symbol] = price
        if self.holdings[symbol] == 0:
            del self.holdings[symbol]
        self.trades.append({"trade_date": trade_date, "symbol": symbol,
                            "quantity": quantity, "price": price, "fee": fee})
        self.orders.append({"trade_date": trade_date, "symbol": symbol,
                            "status": "filled"})

    def mark_to_market(self, bars: pd.DataFrame) -> tuple[float, float, list[dict]]:
        by_symbol = {row["symbol"]: row for _, row in bars.iterrows()}
        rows = []
        for symbol, shares in sorted(self.holdings.items()):
            bar = by_symbol.get(symbol)
            if bar is not None and np.isfinite(bar["close"]) and bar["close"] > 0:
                price = float(bar["close"])
                self.last_close[symbol] = price
                status = "reported_close"
            else:
                price = self.last_close[symbol]
                status = "carried_forward"
            rows.append({"symbol": symbol, "shares": shares,
                         "valuation_price": price, "valuation_status": status,
                         "market_value": shares * price})
        market_value = sum(row["market_value"] for row in rows)
        return market_value, self.cash + market_value, rows


class BacktestEngine:
    def __init__(self, config: BacktestConfig, strategy: Strategy,
                 price_source: DuckDBPriceSource) -> None:
        self.config = config
        self.strategy = strategy
        self.price_source = price_source

    def run(self, benchmark_prices: pd.DataFrame | None = None,
            trading_dates: pd.DatetimeIndex | None = None,
            open_tradability: pd.DataFrame | None = None) -> BacktestResult:
        prices = self.price_source.load(self.config).copy()
        required = {"symbol", "trade_date", "open", "close", "volume_lots", "amount_yuan"}
        if required - set(prices):
            raise ValueError("price source is missing required columns")
        prices["trade_date"] = pd.to_datetime(prices["trade_date"]).dt.normalize()
        if prices.duplicated(["symbol", "trade_date"]).any():
            raise ValueError("duplicate symbol/trade_date rows")
        if open_tradability is not None:
            required_status = {"symbol", "trade_date", "tradable_at_open"}
            if not isinstance(open_tradability, pd.DataFrame) or not required_status.issubset(
                open_tradability.columns
            ):
                raise ValueError("open_tradability requires symbol, trade_date and tradable_at_open")
            status = open_tradability[list(required_status)].copy()
            status["trade_date"] = pd.to_datetime(status["trade_date"]).dt.normalize()
            if status.duplicated(["symbol", "trade_date"]).any() or not status[
                "tradable_at_open"
            ].map(lambda value: isinstance(value, (bool, np.bool_))).all():
                raise ValueError("open_tradability must have unique keys and boolean values")
            prices = prices.merge(status, on=["symbol", "trade_date"], how="left",
                                  validate="one_to_one")
            in_range = prices["trade_date"].between(
                pd.Timestamp(self.config.start_date), pd.Timestamp(self.config.end_date)
            )
            if prices.loc[in_range, "tradable_at_open"].isna().any():
                raise ValueError("open_tradability must cover every stored bar in range")
            prices["tradable_at_open"] = prices["tradable_at_open"].fillna(True).astype(bool)
        if trading_dates is None:
            if self.config.rebalance_frequency != "daily":
                raise ValueError(
                    "weekly/monthly rebalancing requires an independent trading_dates calendar"
                )
            dates = pd.DatetimeIndex(prices["trade_date"].unique()).sort_values()
            calendar_source = "observed_prices"
            calendar_warning = (
                "Observed price dates can omit market sessions; supply an independent "
                "trading_dates calendar for stable scheduling and momentum windows."
            )
        else:
            dates = pd.DatetimeIndex(pd.to_datetime(trading_dates)).normalize()
            if dates.empty or dates.hasnans or dates.has_duplicates or not dates.is_monotonic_increasing:
                raise ValueError("trading_dates must be sorted, unique and non-null")
            # The caller may provide a calendar beginning at the warmup period
            # rather than at the first date ever stored in DuckDB.
            prices = prices[prices["trade_date"] >= dates[0]].copy()
            if not prices["trade_date"].isin(dates).all():
                raise ValueError("trading_dates must include all price dates")
            calendar_source = "provided"
            calendar_warning = None
        signal_dates = dates
        dates = dates[(dates >= pd.Timestamp(self.config.start_date))
                      & (dates <= pd.Timestamp(self.config.end_date))]
        if len(dates) == 0:
            raise ValueError("no trading dates in backtest range")
        broker = Broker(self.config.initial_cash, self.config.commission_rate,
                        self.config.lot_size)
        pending: dict[str, float] | None = None
        curve: list[dict] = []
        positions: list[dict] = []
        previous_equity = self.config.initial_cash
        for index, day in enumerate(dates):
            bars = prices[prices["trade_date"] == day]
            if pending is not None:
                broker.execute(day, bars, pending)
                pending = None
            market_value, equity, daily_positions = broker.mark_to_market(bars)
            curve.append({"trade_date": day, "cash": broker.cash,
                          "market_value": market_value, "equity": equity,
                          "daily_return": equity / previous_equity - 1,
                          "carried_positions": sum(
                              row["valuation_status"] == "carried_forward"
                              for row in daily_positions
                          )})
            positions.extend({"trade_date": day, **row} for row in daily_positions)
            previous_equity = equity
            if index + 1 < len(dates) and self._rebalance_day(day, dates[index + 1]):
                history = prices[prices["trade_date"] <= day].copy()
                weights = self.strategy.target_weights(
                    history, day, signal_dates[signal_dates <= day]
                )
                self._validate_weights(weights)
                pending = weights
        equity_curve = pd.DataFrame(curve)
        if benchmark_prices is not None:
            equity_curve = self._add_benchmark(equity_curve, benchmark_prices)
        return BacktestResult(
            equity_curve,
            pd.DataFrame(broker.trades,
                         columns=["trade_date", "symbol", "quantity", "price", "fee"]),
            pd.DataFrame(broker.orders,
                         columns=["trade_date", "symbol", "status"]),
            pd.DataFrame(positions,
                         columns=["trade_date", "symbol", "shares",
                                  "valuation_price", "valuation_status", "market_value"]),
            calendar_source,
            calendar_warning,
        )

    def _rebalance_day(self, day: pd.Timestamp, next_day: pd.Timestamp) -> bool:
        frequency = self.config.rebalance_frequency
        if frequency == "daily":
            return True
        if frequency == "weekly":
            return day.isocalendar()[:2] != next_day.isocalendar()[:2]
        return (day.year, day.month) != (next_day.year, next_day.month)

    def _validate_weights(self, weights: dict[str, float]) -> None:
        if not isinstance(weights, dict) or any(
            symbol not in self.config.symbols or not np.isfinite(weight) or weight < 0
            for symbol, weight in weights.items()
        ) or sum(weights.values()) > 1 + 1e-12:
            raise ValueError("strategy must return nonnegative weights summing to at most one")

    def _add_benchmark(self, curve: pd.DataFrame,
                       benchmark: pd.DataFrame) -> pd.DataFrame:
        if not {"trade_date", "close"}.issubset(benchmark.columns):
            raise ValueError("benchmark requires trade_date and close")
        data = benchmark[["trade_date", "close"]].copy()
        data["trade_date"] = pd.to_datetime(data["trade_date"]).dt.normalize()
        if data["trade_date"].duplicated().any():
            raise ValueError("duplicate benchmark dates")
        data = curve[["trade_date"]].merge(data, on="trade_date", how="left")
        close = pd.to_numeric(data["close"], errors="coerce")
        if close.isna().any() or (~np.isfinite(close)).any() or (close <= 0).any():
            raise ValueError("benchmark needs positive finite closes on every backtest date")
        curve["benchmark_return"] = close.pct_change().fillna(0).to_numpy()
        curve["benchmark_equity"] = (close / close.iloc[0] * self.config.initial_cash).to_numpy()
        return curve
