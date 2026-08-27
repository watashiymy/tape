"""信号池校验规则（v0.2.2 设计 §3.3）：纯函数，面板与命令行共用，离线可测。

四条规则各自对应一次真实的静默失败：
- **6 位数字**：YAML 1.1 把裸写的 `000333` 当八进制解析成 219，`str()` 之后是
  "219" —— 一个格式合法、看着像模像样、其实完全错误的代码；
- **必须在扫描池内**（主板、非 ST、上市满 400 天）：引擎按主板 ±10% 涨跌停建模，
  ST（±5%）与次新股会让回测口径失真而不报错；
- **去重、保持稳定顺序**：排序会让每次改动的 diff 跳来跳去，看不出真改了什么；
- **至少留 1 只**：空 universe 会让 run_daily_signal 打印误导性的"全部标的数据均
  未更新"（0==0 恒真）退出、run_backtest 在 equal_weight_hold 深处抛
  No objects to concatenate（v0.1.1 已修过一次）。
"""
from __future__ import annotations

import re
from collections.abc import Iterable

# 用 [0-9] 而不是 \d：\d 认全角数字（"６００５１９"），那种代码拿去请求必然失败。
# 用 \A..\Z 而不是 ^..$：$ 允许末尾多一个换行，"600519\n" 会被放行。
SYMBOL_RE = re.compile(r"\A[0-9]{6}\Z")


def validate_symbol(symbol: str) -> str:
    """校验单个代码的格式，返回它本身；不合法抛错。"""
    if not isinstance(symbol, str):
        # 整数一律拒绝，**不做 zfill 补零**：拿到 219 时无从知道原文是 000333 还是
        # 000219，补零等于把一个已知错误伪装成一个合法代码。宁可让调用方显式给字符串。
        raise TypeError(f"股票代码必须是字符串（6 位数字），实际为 {symbol!r}（{type(symbol).__name__}）")
    if not SYMBOL_RE.match(symbol):
        raise ValueError(f"股票代码必须是 6 位数字，实际为 {symbol!r}")
    return symbol


def normalize_universe(symbols: Iterable[str]) -> tuple[str, ...]:
    """逐项校验 + 去重（保留首次出现的位置）+ 非空校验。"""
    out: list[str] = []
    for s in symbols:
        validate_symbol(s)
        if s not in out:
            out.append(s)
    if not out:
        raise ValueError("信号池不能为空：至少要保留 1 只标的")
    return tuple(out)


def pool_symbols(df) -> set[str]:
    """把 `get_all_symbols` 的 DataFrame[symbol, name] 转成代码集合。"""
    if "symbol" not in getattr(df, "columns", []):
        raise ValueError(f"扫描池缺少 symbol 列，实际列为 {list(getattr(df, 'columns', []))}")
    return {str(s) for s in df["symbol"]}


def add_symbol(current: Iterable[str], symbol: str,
               pool: Iterable[str]) -> tuple[str, ...]:
    """把 symbol 追加到池末尾并返回新池；已在池中则原样返回（面板上重复点不该报错）。

    pool 是扫描池代码集合（见 `pool_symbols`）。不在扫描池内 → 抛错。
    """
    validate_symbol(symbol)
    existing = normalize_universe(current)
    if symbol in existing:
        return existing
    if symbol not in set(pool):
        raise ValueError(
            f"{symbol} 不在扫描池内（沪深主板、非 ST、上市满 400 天）；"
            "回测与信号引擎按主板口径建模，池外标的会让结果失真")
    return existing + (symbol,)          # 追加到末尾：diff 只多一行


def remove_symbol(current: Iterable[str], symbol: str) -> tuple[str, ...]:
    """从池中移除 symbol 并返回新池。不依赖扫描池，因此离线也能用。"""
    validate_symbol(symbol)
    existing = normalize_universe(current)
    if symbol not in existing:
        # 多半意味着界面上看到的池子与文件里的已经不同步，必须响亮失败
        raise ValueError(f"{symbol} 不在信号池中：{list(existing)}")
    rest = tuple(s for s in existing if s != symbol)
    if not rest:
        raise ValueError("信号池不能为空：至少要保留 1 只标的")
    return rest
