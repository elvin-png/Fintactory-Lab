# Fintactory-Lab
A Python-based financial data analysis, multi-factor research and quantitative backtesting platform.

当前实现 A 股日线行情的获取、批量增量入库、质量检查、动量因子与横截面 Rank IC 研究，以及单股票 Streamlit 展示。不包含回测或交易功能。使用 Python 3.11+、AkShare、Pandas、DuckDB 和 Streamlit。

## 安装

在项目根目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## 运行页面

```powershell
streamlit run src/finfactor_lab/app.py
```

输入六位 A 股代码，选择日期范围，点击“获取并保存行情”。数据写入 `data/market_data.duckdb`，页面展示该范围内已保存的行情。AkShare 请求需要网络连接，且可能受上游数据源可用性影响。成交量单位为手，价格默认不复权。数据模型见 [docs/data-model.md](docs/data-model.md)。

## 多股票批量更新

先准备 20～50 个六位 A 股代码，用逗号分隔。以下命令中的代码列表只是格式示例，请替换为要研究的股票池：

```powershell
python -m finfactor_lab.batch --symbols "000001,000002,600000" --start 2024-01-01 --end 2024-12-31
```

默认写入 `data/market_data.duckdb`；可通过 `--db` 指定路径。每只股票单独获取、检查和保存，一只失败不影响其他股票；失败结果按股票输出，命令在有失败时返回退出码 1。每只股票最多请求 3 次（初次加 2 次重试），可用 `--retries` 修改。重复运行时，从该股票最新入库交易日前 7 个**自然日**重新获取，以覆盖近期修订；可用 `--overlap-days` 修改。超过重叠窗口的历史缺口，使用 `--full-refresh` 从 `--start` 重新补数。空响应不会推进进度。数据库主键 `(symbol, trade_date)` 防止重复记录，并更新重叠日期的已有价格。

所有请求固定使用 AkShare `stock_zh_a_hist` 的 `period="daily"`、`adjust=""`（不复权）。日期标准化为交易日 `DATE`，代码保留六位字符串，行情数值入库为 `DOUBLE`；价格单位为元，成交量为手，成交额为元。批量入库前检查空值、重复键、非正或非有限价格、OHLC 高低价关系和负成交量/成交额；有问题的股票该次不入库，错误出现在结果中。`check_daily_prices` 还统计相邻获取记录收盘价涨跌幅超过 50% 的次数，并在批量结果中提示，不阻止入库。可用 `finfactor_lab.quality.check_daily_prices` 对读出的多股票 DataFrame 再次检查。

该检查不能判断某个交易日是否整市场缺失，也不把大幅涨跌直接判为异常：不复权价格会受分红、拆股等公司行为影响。AkShare 的可用性、停牌、新上市股票的历史长度和数据修订均可能影响覆盖率；研究时应检查样本期和交易日历。数据库文件、虚拟环境和密钥已被 `.gitignore` 排除。

## 动量因子研究

`finfactor_lab.factor_research` 可从 DuckDB 读取多股票行情，也可直接接收包含多只股票的日线 DataFrame。输入字段为 `symbol`、`trade_date`、`close`、`volume_lots`、`amount_yuan`。公式按市场交易日序号计算：

- **20 日动量**：`close[t] / close[t-20] - 1`，只使用当日及之前的数据。
- **未来 5 日收益标签**：`close[t+5] / close[t] - 1`，仅用于事后评价，不参与因子计算。
- **日度 Rank IC**：在同一 `trade_date` 上，对有效股票的动量与未来收益分别做平均秩排序，再求 Pearson 相关系数，即 Spearman 相关系数。至少需要 3 只有效股票；任一变量的秩全部相同则结果为缺失值。单只股票的时间序列相关性不算 Rank IC。

缺失行、空值、非正收盘价，以及成交量或成交额为零的停牌行均视为无效。动量要求从 `t-20` 到 `t` 的 21 个交易日全部有效；标签要求从 `t` 到 `t+5` 的 6 个交易日全部有效。样本不足时返回 `NaN`，不填充价格，也不跨过停牌日顺延窗口。建议传入完整市场交易日历；省略时使用输入数据的日期并集，若所有股票同日都缺行，该日期无法被自动识别。当前价格为不复权价格，公司行为可能影响动量和收益解释。

已保存多只股票行情时，可以在项目根目录运行以下示例。请将代码和日期范围改为实际已保存的数据；需要覆盖 20 日回看及 5 日后看：

```python
from datetime import date
from finfactor_lab.factor_research import build_research_panel_from_db, calculate_rank_ic

symbols = ["000001", "000002", "000003"]
panel = build_research_panel_from_db(
    symbols, date(2024, 1, 1), date(2024, 6, 30),
    # 有完整市场交易日历时传入 trading_dates=...
)
daily_ic = calculate_rank_ic(panel)
print(daily_ic.dropna(subset=["rank_ic"]))
```

当前行情页面仍按单只股票获取和展示。若本地只有一只股票，先用下述模拟多股票测试验证横截面 IC，不要把单股票结果解释为有效 IC。

## 测试

```powershell
python -m pytest
```

测试使用模拟的 AkShare 返回数据、25 股票行情、临时 DuckDB 文件及 Streamlit 页面模拟，无需实时行情网络请求。模拟 Rank IC 在指定日期为 0.5；这是方法验证，不代表实际市场结果。
