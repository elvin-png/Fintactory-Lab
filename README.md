# Fintactory-Lab
A Python-based financial data analysis, multi-factor research and quantitative backtesting platform.

当前第一阶段实现 A 股日线行情的获取、标准化、保存和展示，不包含回测或交易功能。使用 Python 3.11+、AkShare、Pandas、DuckDB 和 Streamlit。

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

## 动量因子研究

`finfactor_lab.factor_research` 接收包含多只股票的日线 DataFrame，不改变现有行情获取、DuckDB 表或 Streamlit 页面。输入字段为 `symbol`、`trade_date`、`close`、`volume_lots`、`amount_yuan`。公式按市场交易日序号计算：

- **20 日动量**：`close[t] / close[t-20] - 1`，只使用当日及之前的数据。
- **未来 5 日收益标签**：`close[t+5] / close[t] - 1`，仅用于事后评价，不参与因子计算。
- **日度 Rank IC**：在同一 `trade_date` 上，对有效股票的动量与未来收益分别做平均秩排序，再求 Pearson 相关系数，即 Spearman 相关系数。至少需要 3 只有效股票；任一变量的秩全部相同则结果为缺失值。单只股票的时间序列相关性不算 Rank IC。

缺失行、空值、非正收盘价，以及成交量或成交额为零的停牌行均视为无效。动量要求从 `t-20` 到 `t` 的 21 个交易日全部有效；标签要求从 `t` 到 `t+5` 的 6 个交易日全部有效。样本不足时返回 `NaN`，不填充价格，也不跨过停牌日顺延窗口。建议传入完整市场交易日历；省略时使用输入数据的日期并集，若所有股票同日都缺行，该日期无法被自动识别。当前价格为不复权价格，公司行为可能影响动量和收益解释。

已保存多只股票行情时，可以在项目根目录运行以下示例。请将代码和日期范围改为实际已保存的数据；需要覆盖 20 日回看及 5 日后看：

```python
from datetime import date
import pandas as pd
from finfactor_lab.storage import load_daily_prices
from finfactor_lab.factor_research import build_research_panel, calculate_rank_ic

symbols = ["000001", "000002", "000003"]
prices = pd.concat(
    [load_daily_prices(s, date(2024, 1, 1), date(2024, 6, 30)) for s in symbols],
    ignore_index=True,
)
panel = build_research_panel(prices)  # 有完整交易日历时传入 trading_dates=...
daily_ic = calculate_rank_ic(panel)
print(daily_ic.dropna(subset=["rank_ic"]))
```

当前仓库的行情页面按单只股票获取和展示。若本地只有一只股票，先用下述模拟多股票测试验证横截面 IC，不要把单股票结果解释为有效 IC。

## 测试

```powershell
python -m pytest
```

测试使用模拟的 AkShare 返回数据、多股票行情、临时 DuckDB 文件及 Streamlit 页面模拟，无需实时行情网络请求。
