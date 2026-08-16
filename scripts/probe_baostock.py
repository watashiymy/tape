# scripts/probe_baostock.py — 一次性探针，验证 API 行为，不进入正式代码
import baostock as bs

lg = bs.login()
print("login:", lg.error_code, lg.error_msg)

rs = bs.query_history_k_data_plus(
    "sh.600519", "date,open,high,low,close,volume,amount,tradestatus,isST",
    start_date="2024-01-01", end_date="2024-01-15", frequency="d", adjustflag="3")
df = rs.get_data()
print("K线列:", list(df.columns)); print(df.head(3))

rs2 = bs.query_adjust_factor(code="sh.600519", start_date="1990-01-01", end_date="2024-12-31")
fac = rs2.get_data()
print("复权因子列:", list(fac.columns)); print(fac.to_string())
# 关键确认：backAdjustFactor 必须是"自上市累积"口径（随时间单调不减、只在除权日出现新记录），
# Task 4 的 ffill 到日频才成立。若它是"单次事件因子"（每条都接近 1），必须改为累乘后再 ffill。

rs3 = bs.query_trade_dates(start_date="2024-01-01", end_date="2024-01-15")
print(rs3.get_data().head(5))

rs4 = bs.query_history_k_data_plus(
    "sh.000300", "date,close", start_date="2024-01-01", end_date="2024-01-15",
    frequency="d", adjustflag="3")
print("指数:", rs4.get_data().head(3))
bs.logout()
