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

## 测试

```powershell
python -m pytest
```

测试使用模拟的 AkShare 返回数据、临时 DuckDB 文件及 Streamlit 页面模拟，无需实时行情网络请求。
