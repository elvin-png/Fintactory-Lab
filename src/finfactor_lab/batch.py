"""Incremental, isolated batch ingestion of A-share daily prices."""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from finfactor_lab.market_data import fetch_daily_prices, validate_symbol
from finfactor_lab.quality import check_daily_prices
from finfactor_lab.storage import DEFAULT_DB_PATH, latest_trade_date, save_daily_prices


@dataclass(frozen=True)
class SymbolResult:
    symbol: str
    status: str
    rows: int
    attempts: int
    start_date: date | None
    error: str | None = None
    extreme_returns: int = 0


def update_many(
    symbols: Iterable[str],
    start_date: date,
    end_date: date,
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    retries: int = 2,
    overlap_days: int = 7,
    full_refresh: bool = False,
    fetcher: Callable[..., pd.DataFrame] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[SymbolResult]:
    """Fetch each symbol independently; upsert a short overlap for revisions.

    An empty result is recorded as empty, rather than advancing a cursor.
    Failed symbols retain their prior rows and are retried on the next run.
    """
    if not isinstance(start_date, date) or not isinstance(end_date, date):
        raise TypeError("开始和结束日期必须是 date")
    if start_date > end_date:
        raise ValueError("开始日期不能晚于结束日期")
    if retries < 0 or overlap_days < 0:
        raise ValueError("retries 和 overlap_days 不能为负数")
    unique_symbols = list(dict.fromkeys(validate_symbol(s) for s in symbols))
    results = []
    for symbol in unique_symbols:
        attempts = 0
        fetch_start = None
        try:
            latest = latest_trade_date(symbol, db_path)
            fetch_start = (
                max(start_date, latest - timedelta(days=overlap_days))
                if latest and not full_refresh else start_date
            )
            if fetch_start > end_date:
                results.append(SymbolResult(symbol, "up_to_date", 0, 0, fetch_start))
                continue
            for attempt in range(retries + 1):
                attempts = attempt + 1
                try:
                    prices = fetch_daily_prices(
                        symbol, fetch_start, end_date, fetcher=fetcher
                    )
                    report = check_daily_prices(prices)
                    if not report.clean:
                        raise ValueError(f"行情质量检查失败: {asdict(report)}")
                    rows = save_daily_prices(prices, db_path)
                    results.append(SymbolResult(
                        symbol, "empty" if rows == 0 else "saved",
                        rows, attempts, fetch_start,
                        extreme_returns=report.extreme_returns,
                    ))
                    break
                except Exception as exc:
                    if attempt == retries:
                        results.append(SymbolResult(
                            symbol, "failed", 0, attempts, fetch_start,
                            f"{type(exc).__name__}: {exc}",
                        ))
                    else:
                        sleep(2 ** attempt)
        except Exception as exc:
            results.append(SymbolResult(
                symbol, "failed", 0, attempts, fetch_start,
                f"{type(exc).__name__}: {exc}",
            ))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="批量增量更新 A 股日线行情")
    parser.add_argument("--symbols", required=True, help="逗号分隔的六位股票代码")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--overlap-days", type=int, default=7)
    parser.add_argument("--full-refresh", action="store_true",
                        help="从 --start 重新获取，用于补历史缺口")
    args = parser.parse_args()
    results = update_many(
        args.symbols.split(","), args.start, args.end, args.db,
        retries=args.retries, overlap_days=args.overlap_days,
        full_refresh=args.full_refresh,
    )
    for result in results:
        print(json.dumps(asdict(result), ensure_ascii=False, default=str))
    return 1 if any(result.status == "failed" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
