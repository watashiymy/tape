"""「市场概览」首页的数据层（spec §7）。

盘中行情来自门户网站的公开接口（腾讯/新浪/东方财富 push2ex），收盘后的宽度来自
baostock 全市场快照。判断与格式化全在本包的纯函数里，页面只排版。
本包不 import app/。
"""
from __future__ import annotations

from datetime import datetime, time

#: 收盘数据（baostock 全市场快照）的就绪时刻。**唯一出处**：guide.FACTS["breadth_ready"]
#: 与任务脚本都引用它。暂定 17:30（baostock 文档值）；实测 15:44 仍空、19:16 已有，
#: 实现计划 Task 26 按 §10 的实测结果修正。**不复用** FACTS["data_ready"]——那是另一个接口。
BREADTH_READY = time(17, 30)

#: 首页六张指数卡片：(腾讯/新浪代码, 显示名)。沪深300 用沪市代码 sh000300，不是 sz399300。
INDEXES: tuple[tuple[str, str], ...] = (
    ("sh000001", "上证指数"), ("sz399001", "深证成指"), ("sz399006", "创业板指"),
    ("sh000688", "科创50"), ("sh000300", "沪深300"), ("sh000852", "中证1000"),
)

WINDOW_TRADING_DAYS = 310           # MA200 + 52 周 250 + 小图 60（spec §5.2）


def now() -> datetime:
    """页面与任务取当前时刻的唯一入口。全项目假定本机时钟即北京时间。
    测试 monkeypatch 这一个函数就能冻结整页的时钟（spec §8「全局隔离」）。"""
    return datetime.now()


def breadth_ready(now: datetime) -> bool:
    """收盘数据（baostock 快照）此刻是否已到就绪时刻 BREADTH_READY。"""
    return now.time() >= BREADTH_READY
