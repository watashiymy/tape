"""策略注册表：配置名 → 策略类（Task 7 加入 donchian）。"""
from quant.strategy.base import Strategy
from quant.strategy.ma_cross import MaCross

REGISTRY: dict[str, type[Strategy]] = {"ma_cross": MaCross}


def build_strategies(strategy_cfg: dict[str, dict]) -> list[Strategy]:
    return [REGISTRY[name](**params) for name, params in strategy_cfg.items()]
