"""策略注册表：配置名 → 策略类（Task 7 加入 donchian）。"""
from quant.strategy.base import Strategy
from quant.strategy.donchian import Donchian
from quant.strategy.ma_cross import MaCross

REGISTRY: dict[str, type[Strategy]] = {"ma_cross": MaCross, "donchian": Donchian}


def build_strategies(strategy_cfg: dict[str, dict]) -> list[Strategy]:
    for name in strategy_cfg:
        if name not in REGISTRY:
            # 裸 KeyError: 'ma_corss' 没有上下文——报 ValueError 并列出可用策略名
            raise ValueError(f"未知策略 {name}，可用: {list(REGISTRY)}")
    return [REGISTRY[name](**params) for name, params in strategy_cfg.items()]
