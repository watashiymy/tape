# tests/test_strategy_registry.py
import pandas as pd
import pytest

from quant.strategy import REGISTRY, build_strategies, strategy_label
from quant.strategy.base import Strategy


def test_unknown_strategy_name_raises_valueerror_listing_available():
    """回归：REGISTRY 未命中旧代码抛裸 KeyError: 'ma_corss'——没有上下文，
    用户不知道是配置键名拼错还是代码坏了。要报 ValueError 并列出可用策略名。"""
    with pytest.raises(ValueError, match="未知策略 ma_corss") as e:
        build_strategies({"ma_corss": {"fast": 20, "slow": 60}})
    for name in REGISTRY:
        assert name in str(e.value), f"报错须列出可用策略 {name}"


def test_known_strategies_still_build():
    """反向断言：合法配置照常构造，且顺序与配置一致。"""
    got = build_strategies({
        "ma_cross": {"fast": 20, "slow": 60},
        "donchian": {"entry_n": 20, "exit_n": 10, "amount_n": 20, "amount_ratio": 1.5},
    })
    assert [s.name for s in got] == ["ma_cross", "donchian"]


# ================================================================ 显示名（v0.4.0 M1）
# 内部键（name）已渗入用户历史数据（扫描 CSV 的 strategy 列、回测目录名、
# journal 的 source 合法值），永不改；label 是加给人看的那一层。


def test_every_registered_strategy_has_a_unique_nonempty_label():
    """注册表驱动：新策略漏写 label 这条会红——UI 上一个裸键混在中文名里，
    正是本次要消灭的东西。label 互不相同：两个策略共用一个显示名，
    回测选择器与来源筛选就分不出谁是谁。"""
    labels: dict[str, str] = {}
    for key, cls in REGISTRY.items():
        label = getattr(cls, "label", "")
        assert isinstance(label, str) and label.strip(), f"策略 {key} 缺显示名 label"
        assert label != key, f"策略 {key} 的 label 不该照抄内部键"
        labels[key] = label
    assert len(set(labels.values())) == len(labels), f"label 有重复: {labels}"


def test_builtin_labels_use_the_industry_standard_chinese_names():
    """行业通行译名，不自创（设计 §1.2 指名的两个）。"""
    assert strategy_label("ma_cross") == "双均线交叉"
    assert strategy_label("donchian") == "唐奇安通道突破"


def test_strategy_label_returns_unknown_keys_verbatim():
    """未知键**原样返回**，绝不抛错：旧产物（扫描 CSV、回测目录、journal 的
    source）里可能存着已下架策略的键，显示层必须永远能显示它们。
    控制台下拉的「全部」这种非策略选项也靠这条通行。"""
    assert strategy_label("turtle") == "turtle"
    assert strategy_label("全部") == "全部"
    assert strategy_label("") == ""


def test_strategy_label_follows_registry_mutations():
    """label 必须**每次现查** REGISTRY，不是 import 时抄一份快照：
    否则测试或未来的插件注册进来的策略永远显示裸键。"""

    class Fake(Strategy):
        name = "fake_v99"
        label = "假策略"

        def generate_positions(self, df: pd.DataFrame) -> pd.Series:
            return pd.Series(0, index=df.index, dtype=int)

    assert strategy_label("fake_v99") == "fake_v99", "未注册时应原样返回"
    REGISTRY["fake_v99"] = Fake
    try:
        assert strategy_label("fake_v99") == "假策略"
    finally:
        del REGISTRY["fake_v99"]
