"""策略基类：输入清洗后的行情 df（含 adj_* 列），输出目标仓位（spec 决策10）。"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    name: str = "base"
    #: 给人看的中文显示名（v0.4.0 M1）。内部键 `name` 已渗入用户历史数据
    #: （扫描 CSV 的 strategy 列、回测目录名、journal 的 source），**永不改**；
    #: 想改显示只改这里。注册进 REGISTRY 的策略必须写非空且互不相同的 label
    #: （tests/test_strategy_registry.py 注册表驱动地钉着，漏写会红）。
    label: str = ""

    @abstractmethod
    def generate_positions(self, df: pd.DataFrame) -> pd.Series:
        """返回与 df.index 对齐的 Series，值 ∈ {0, 1}；只能使用当日及以前的数据。"""
