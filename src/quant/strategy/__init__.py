"""策略注册表：配置名 → 策略类（Task 7 加入 donchian）。"""
from quant.strategy.base import Strategy
from quant.strategy.donchian import Donchian
from quant.strategy.ma_cross import MaCross

REGISTRY: dict[str, type[Strategy]] = {"ma_cross": MaCross, "donchian": Donchian}


def strategy_label(key: str) -> str:
    """内部键 → 中文显示名；未知键**原样返回**（v0.4.0 M1）。

    原样返回不是宽容，是兼容契约：扫描 CSV、回测目录名、journal 的 source
    里存的都是键，旧产物里可能有已下架策略的键——显示层绝不能因此抛错，
    否则一份老 CSV 就能把页面打没。每次现查 REGISTRY（不在 import 时抄快照），
    新注册的策略立刻有显示名。控制台下拉的「全部」这类非策略选项也靠这条通行。
    """
    cls = REGISTRY.get(key)
    return cls.label if cls is not None else key


def build_strategies(strategy_cfg: dict[str, dict]) -> list[Strategy]:
    for name in strategy_cfg:
        if name not in REGISTRY:
            # 裸 KeyError: 'ma_corss' 没有上下文——报 ValueError 并列出可用策略名
            raise ValueError(f"未知策略 {name}，可用: {list(REGISTRY)}")
    return [REGISTRY[name](**params) for name, params in strategy_cfg.items()]
