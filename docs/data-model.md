# 日线数据模型

`daily_prices` 以 `(symbol, trade_date)` 为主键。重复获取同一交易日时更新该行。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `symbol` | VARCHAR | 六位 A 股代码，保留前导零 |
| `trade_date` | DATE | 交易日期 |
| `open`, `high`, `low`, `close` | DOUBLE | 不复权价格，元 |
| `volume_lots` | DOUBLE | 成交量，手 |
| `amount_yuan` | DOUBLE | 成交额，元 |

数据由 AkShare `stock_zh_a_hist` 获取。网络请求发生在用户点击页面的“获取并保存行情”按钮时，或显式运行 `python -m finfactor_lab.batch` 时；页面展示从本地 DuckDB 读取。批量更新用最新交易日减去重叠窗口作为下一次请求起点，并通过主键 UPSERT。`load_many_daily_prices` 按日期和代码读取股票池，供因子研究使用。
