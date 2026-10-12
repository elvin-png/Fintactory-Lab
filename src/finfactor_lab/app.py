"""Streamlit page for fetching and viewing stored A-share daily prices."""

from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from finfactor_lab.market_data import fetch_daily_prices, validate_symbol
from finfactor_lab.storage import load_daily_prices, save_daily_prices


st.set_page_config(page_title="FinFactor Lab", layout="wide")
st.title("A 股日线行情")
st.caption("通过 AkShare 获取不复权日线行情，保存到本地 DuckDB。成交量单位为手。")

symbol = st.text_input("股票代码", value="000001", max_chars=6)
left, right = st.columns(2)
with left:
    start_date = st.date_input("开始日期", value=date.today() - timedelta(days=365))
with right:
    end_date = st.date_input("结束日期", value=date.today())

if st.button("获取并保存行情", type="primary"):
    try:
        validate_symbol(symbol)
        prices = fetch_daily_prices(symbol, start_date, end_date)
        count = save_daily_prices(prices)
        st.success(f"已保存 {count} 条行情记录。")
    except Exception as exc:
        st.error(f"获取或保存失败：{exc}")

try:
    validate_symbol(symbol)
    stored = load_daily_prices(symbol, start_date, end_date)
    if stored.empty:
        st.info("当前日期范围没有已保存的行情。")
    else:
        st.subheader(f"{symbol} 行情（{len(stored)} 条）")
        st.line_chart(stored.set_index("trade_date")["close"])
        st.dataframe(stored, use_container_width=True, hide_index=True)
except ValueError as exc:
    st.warning(str(exc))
except Exception as exc:
    st.error(f"读取本地行情失败：{exc}")
