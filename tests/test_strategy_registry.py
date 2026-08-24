# tests/test_strategy_registry.py
import pytest

from quant.strategy import REGISTRY, build_strategies


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
