# tests/test_strategy_pipeline.py — v0.4.0 M2：信号流水线收拢 + ATR 追踪止损（设计 §2）
#
# 两件事在这里被钉住：
#   1. **收拢**：回测/每日信号/全市场扫描三个入口的目标仓位只有一个出口
#      （quant.strategy.pipeline.target_positions）。此前三处各自调
#      strat.generate_positions(df)，叠加层只要漏接一处，就会出现"扫描说买、
#      回测按另一套规则算、每日信号又是第三套"——静默分叉，且三边都不报错。
#   2. **ATR 追踪止损的语义**：阈值手算、止损后不得回补、暖机期不装算得出来、
#      截断重放无未来函数、关闭时是恒等变换。
import ast
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from quant.config import AtrStopCfg, OverlaysCfg
from quant.indicators import atr
from quant.signal.market_scan import MIN_BARS, classify_and_scan
from quant.signal.scan import scan
from quant.strategy.base import Strategy
from quant.strategy.donchian import Donchian
from quant.strategy.ma_cross import MaCross
from quant.strategy.pipeline import target_positions
from tests.conftest import make_bars

ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = ROOT / "scripts" / "run_backtest.py"
_SPEC = importlib.util.spec_from_file_location("run_backtest_m2", _SCRIPT)
run_backtest = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(run_backtest)

EXPECTED = pd.Timestamp("2024-06-28").date()   # 周五，bdate_range(end=...) 的最后一根

OFF = OverlaysCfg()                            # 无 overlays 段 = 全部禁用


def stop(n: int = 4, k: float = 3.0) -> OverlaysCfg:
    return OverlaysCfg(atr_stop=AtrStopCfg(enabled=True, n=n, k=k))


def bars(adj_closes, factors=None, end="2024-06-28") -> pd.DataFrame:
    """按**后复权收盘价路径**造行情，最后一根落在 end。

    adj_high = adj_low = adj_close，于是 TR = |Δadj_close|（首根退化为 high−low = 0），
    ATR 可以逐根手推——这是本文件全部手算断言的前提。
    raw = adj / factor：factor 带台阶（除权日）时 raw ≠ adj，
    "止损误用原始价"这一错法才测得出来（spec 决策1）。
    """
    factors = factors or [1.0] * len(adj_closes)
    idx = pd.bdate_range(end=end, periods=len(adj_closes))
    rows = [dict(date=d.strftime("%Y-%m-%d"), open=a / f, high=a / f, low=a / f,
                 close=a / f, volume=1000, amount=1e8, adj_factor=f)
            for d, a, f in zip(idx, adj_closes, factors)]
    df = make_bars(rows)
    for c in ("open", "high", "low", "close"):
        df["adj_" + c] = df[c] * df["adj_factor"]
    df.attrs["symbol"], df.attrs["name"] = "600000", "浦发银行"
    return df


class Fixed(Strategy):
    """基础信号由用例直接给定：本组要钉的是 overlay 的语义，不是某个策略的语义。

    对**截断后**的 df 返回仓位前缀——截断重放测试要求策略自己也无未来函数。
    """
    name = "fixed_stub"
    label = "固定桩"

    def __init__(self, positions):
        self.positions = list(positions)

    def generate_positions(self, df: pd.DataFrame) -> pd.Series:
        return pd.Series(self.positions[:len(df)], index=df.index, dtype=int)


# ============================================================ 手算基线（n=4, k=3.0）
#
# 两条序列都：先涨到 peak=120（入场），随后每根等幅下跌 q，末根的 ATR 恰为 30，
# 阈值 k×ATR = 90。差别只在回撤是 2.9×ATR 还是 3.1×ATR。
#
# A) 涨幅 33、q=29：TR = [0, 33, 33, 29, 29, 29]
#    末根 ATR(4) = (33+29+29+29)/4 = 120/4 = 30；阈值 3×30 = 90
#    回撤 = peak − close = 120 − 33 = 87 = 2.9 × 30  →  87 < 90 **不触发**
# B) 涨幅 27、q=31：TR = [0, 27, 27, 31, 31, 31]
#    末根 ATR(4) = (27+31+31+31)/4 = 120/4 = 30；阈值 3×30 = 90
#    回撤 = 120 − 27 = 93 = 3.1 × 30  →  93 > 90 **触发**
#
# 中途各根都手工验过不会提前触发（见 test_* 里逐根的断言）。
NOT_TRIGGERED = [54.0, 87.0, 120.0, 91.0, 62.0, 33.0]
TRIGGERED = [66.0, 93.0, 120.0, 89.0, 58.0, 27.0]
ENTER_AT_2 = [0, 0, 1, 1, 1, 1]        # 第 2 根入场（peak = 该根收盘 120）


def test_atr_hand_computed_baseline_for_both_series():
    """先把手算的 ATR 钉住：后面两条阈值断言全部建立在"末根 ATR = 30"之上，
    这里若变了（indicators.atr 的口径被改），下面的 2.9/3.1 就不再是 2.9/3.1，
    而断言仍会"通过"——那是最坏的一种测试。"""
    for closes, warm in ((NOT_TRIGGERED, 23.75), (TRIGGERED, 21.25)):
        a = atr(bars(closes), 4)
        assert a.iloc[:3].isna().all(), "ATR(4) 前 3 根必须是 NaN（首根 TR 退化为 high−low）"
        assert a.iloc[3] == pytest.approx(warm)
        assert a.iloc[5] == pytest.approx(30.0)


def test_a_drawdown_of_2p9_atr_does_not_trigger():
    """回撤 87 = 2.9 × ATR(30) < 阈值 90 → 一根都不该被止掉。

    这条是止损的"松"侧：k 取 3 而不是海龟经典的 2，正是因为既有实测证明
    "出场太急、一次正常回调就被甩下车"是唐奇安跑输的主因（设计 §2.2）。
    """
    df = bars(NOT_TRIGGERED)
    pos = target_positions(df, Fixed(ENTER_AT_2), stop())
    # 逐根手算：D=回撤，thr=3×ATR
    #  idx3: D=29,  ATR=(0+33+33+29)/4=23.75, thr=71.25
    #  idx4: D=58,  ATR=(33+33+29+29)/4=31.00, thr=93.00
    #  idx5: D=87,  ATR=(33+29+29+29)/4=30.00, thr=90.00  ← 2.9×，不触发
    assert list(pos) == [0, 0, 1, 1, 1, 1]


def test_a_drawdown_of_3p1_atr_triggers_on_that_very_bar():
    """回撤 93 = 3.1 × ATR(30) > 阈值 90 → 当根目标仓位归 0。

    归 0 落在**触发当根**（T 收盘），引擎既有的 desired = positions.shift(1)
    使其 T+1 开盘成交，与所有既有信号同一时序——止损没有自己的特权时序。
    """
    df = bars(TRIGGERED)
    pos = target_positions(df, Fixed(ENTER_AT_2), stop())
    #  idx3: D=31, ATR=(0+27+27+31)/4=21.25, thr=63.75
    #  idx4: D=62, ATR=(27+27+31+31)/4=29.00, thr=87.00
    #  idx5: D=93, ATR=(27+31+31+31)/4=30.00, thr=90.00  ← 3.1×，触发
    assert list(pos) == [0, 0, 1, 1, 1, 0]


def test_the_stop_reads_adjusted_close_not_raw_close():
    """peak 与当日价一律用后复权价。

    factors 在 idx3 从 1 跳到 2（10 送 10 除权），raw 价当根腰斩到 44.5：
    误用 raw close 会把"除权"当成"暴跌"，在 idx3 就止损（D_raw = 75.5 > 71.25 那档），
    整段持仓被凭空砍掉两根。正确答案仍是 idx5 触发。
    """
    df = bars(TRIGGERED, factors=[1.0, 1.0, 1.0, 2.0, 2.0, 2.0])
    assert df["close"].iloc[3] == pytest.approx(44.5)      # raw 确实腰斩了
    pos = target_positions(df, Fixed(ENTER_AT_2), stop())
    assert list(pos) == [0, 0, 1, 1, 1, 0], "误用原始价会在除权日 idx3 就止损"


def test_the_entry_bar_can_never_stop_out_on_itself():
    """peak 只从**入场那根**起算，因此入场当根回撤恒为 0 < k×ATR，不可能自己把自己止掉。

    入场前先暴跌（100 → 10，把 ATR 顶到 22.5、阈值 67.5），入场落在低位那根：
    若 peak 误用"全样本滚动最高价"，入场当根的回撤会算成 100−10 = 90 > 67.5，
    每次入场都立刻被止掉——回测里只会看到一串零收益往返，看不出病因。
    """
    df = bars([100.0, 100.0, 100.0, 10.0, 10.0, 10.0])
    assert atr(df, 4).iloc[3] == pytest.approx(22.5)     # (0+0+0+90)/4
    pos = target_positions(df, Fixed([0, 0, 0, 1, 1, 1]), stop())
    assert list(pos) == [0, 0, 0, 1, 1, 1]


# ============================================================ 止损后不得回补（核心）
#
# 止损次日基础信号往往还是 1（双均线仍在多头排列），若据此立刻回补，
# 止损就形同虚设——买回的还是那只刚刚跌破止损线的票。必须等基础策略给出
# **新的 0→1**。

BLOCKED = TRIGGERED + [40.0, 60.0, 90.0, 120.0, 150.0]   # idx5 止损后一路反弹


def test_a_base_signal_that_stays_1_never_re_arms_after_a_stop():
    """基础信号一路 1，止损后必须**一直**空仓——哪怕价格反弹回 peak 之上。

    这条是本里程碑最容易写错的一条：把 overlay 实现成"逐根重算 peak 与阈值"
    （无状态）时，止损次日 D 变小、条件不再成立，仓位自动回补，
    回测里看不出任何异常——只是止损一次也没生效。
    """
    pos = target_positions(bars(BLOCKED), Fixed([0, 0] + [1] * 9), stop())
    assert list(pos) == [0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 0]
    assert bars(BLOCKED)["adj_close"].iloc[-1] > 120.0, "反弹确实超过了旧 peak，才有回补的诱惑"


def test_a_fresh_0_to_1_base_entry_re_arms_the_position():
    """封锁只解除于基础策略的**新入场**（0→1）：idx9 基础信号回 0，idx10 再给 1 → 重新持有。
    否则一次止损就把这只票永久拉黑，策略后半段的全部机会凭空消失。"""
    base = [0, 0, 1, 1, 1, 1, 1, 1, 1, 0, 1]
    pos = target_positions(bars(BLOCKED), Fixed(base), stop())
    assert list(pos) == [0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 1]


#: 二次入场（idx10，peak = 150）之后每根跌 30，用来分开"新 peak"与"旧 peak"两种口径
REENTRY = BLOCKED + [120.0, 90.0, 60.0, 30.0]
REENTRY_BASE = [0, 0, 1, 1, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1]


def test_re_entry_starts_a_brand_new_peak():
    """重新入场后 peak 从**新**入场价起算，不复用被止损那一轮的旧 peak。

    idx10 以 150 入场（上一轮的旧 peak 是 120），随后每根跌 30（TR=30 → ATR(4)=30，
    阈值 3×30 = 90）：
      idx13: c=60 → 新 peak 口径回撤 = 150−60 = 90，**恰好等于**阈值 → 判据是 <，不触发
      idx14: c=30 → 新 peak 口径回撤 = 120 > 90 → 触发
                    旧 peak 口径回撤 = 120−30 = 90，不触发 → 会多持一根
    idx13 顺带钉住"回撤 == 阈值不触发"这条边界（写成 <= 会让止损整体提前一档）。
    """
    pos = target_positions(bars(REENTRY), Fixed(REENTRY_BASE), stop())
    assert list(pos) == [0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 1, 1, 1, 1, 0]


def test_a_base_exit_clears_the_block_too():
    """基础策略自己先出场（1→0）也把封锁清掉：此后的 0→1 是一次干净的新入场。
    这条与上一条的区别是"先止损再由基础信号离场"的顺序，写状态机时极易漏。"""
    base = [0, 0, 1, 1, 1, 1, 0, 0, 1, 1, 1]
    pos = target_positions(bars(BLOCKED), Fixed(base), stop())
    #                            ↑ idx5 止损     ↑ idx8 新入场（peak = 90）
    assert list(pos) == [0, 0, 1, 1, 1, 0, 0, 0, 1, 1, 1]


# ============================================================ 暖机期 / 无未来函数 / 恒等


def test_a_nan_atr_during_warmup_never_triggers():
    """ATR 还没成形就不装算得出来：暖机期一律不触发止损。

    ATR(4) 的 NaN 落在前 3 根（**不是** 4 根：首根没有昨收，TR 按惯例退化为
    当日 high−low，见 tests/test_indicators.py）。idx2 已经暴跌 99%，
    但 ATR 是 NaN——NaN 参与比较恒为 False，这里必须靠显式的"算不出来就不动"，
    而不是靠比较的副作用（那样一旦改成 `not (close >= thr)` 就静默反转）。
    """
    df = bars([1000.0, 1000.0, 10.0, 10.0])
    a = atr(df, 4)
    assert a.iloc[:3].isna().all() and a.iloc[3] == pytest.approx(247.5)  # (0+0+990+0)/4
    pos = target_positions(df, Fixed([1, 1, 1, 1]), stop())
    # idx2: D=990 但 ATR=NaN → 不触发；idx3: ATR 成形，thr=3×247.5=742.5 < 990 → 触发
    assert list(pos) == [1, 1, 1, 0]


@pytest.mark.parametrize("cut", range(1, len(REENTRY) + 1))
def test_truncated_replay_reproduces_the_full_run(cut):
    """截断重放：只喂前 cut 根，前 cut 根的结论必须与全量一致 = 无未来函数。
    这是本项目对每个信号生成器的固定体检（见 test_ma_cross / test_donchian）。"""
    full = target_positions(bars(REENTRY), Fixed(REENTRY_BASE), stop())
    trunc = target_positions(bars(REENTRY[:cut]), Fixed(REENTRY_BASE), stop())
    assert list(trunc) == list(full)[:cut]


@pytest.mark.parametrize("strat", [
    MaCross(fast=2, slow=4),
    Donchian(entry_n=3, exit_n=2, amount_n=3, amount_ratio=0.5),
    Fixed([0, 1, 0, 1, 1, 1, 0, 0, 1, 1, 1]),
])
def test_a_disabled_overlay_is_the_identity_transform(strat):
    """overlay 全关 → pipeline 的输出**逐位等于**基础信号（含 dtype 与 name）。

    这条守的是向后兼容：v0.4.0 之前的全部结论（README 里的实测数字、既有回测产物）
    都是无叠加层口径，关掉 overlay 必须能一字不差地回到那个口径。
    """
    df = bars(BLOCKED)
    pd.testing.assert_series_equal(target_positions(df, strat, OFF),
                                   strat.generate_positions(df))


#: zigzag 100/101 × 22 根（TR=1）后暴跌到 50——专为"默认参数 n=20 也能触发"设计
CRASH = [100.0 + (i % 2) for i in range(22)] + [50.0]


def test_the_enabled_flag_is_actually_consulted_not_just_warmup_luck():
    """关闭态的恒等必须是"开关被尊重"的结果，而不是"样本短于暖机期所以止损无话可说"。

    上面那条恒等测试的 df 只有 11 根 < 默认 n=20：一个无视 enabled、永远套止损的
    变异体在那里活得好好的（ATR 全 NaN，套了等于没套——变异实验实测存活）。
    本条用 23 根、末根暴跌的序列把两种口径分开，手算（bars() 里 TR = |Δadj_close|）：
      TR: idx0 = 0（首根退化为 high−low），idx1..21 = 1，idx22 = |50−101| = 51
      ATR(20)@idx22 = (19×1 + 51)/20 = 3.5 → 阈值 3×3.5 = 10.5
      回撤 = peak − c = 101 − 50 = 51 > 10.5 → 触发
      此前不触发：ATR 首个非 NaN 在 idx19 = (0+19×1)/20 = 0.95（阈值 2.85，回撤 0）；
      idx20 ATR = 1（阈值 3，回撤 1）；idx21 回撤 0。
    先证明**开着**默认参数确实会在末根止损（测试有分辨力），再断言**关着**逐位等于
    基础信号——恒等因此是 enabled=False 的功劳，不是暖机的巧合。
    """
    df = bars(CRASH)
    strat = Fixed([1] * len(CRASH))
    on = target_positions(df, strat, OverlaysCfg(atr_stop=AtrStopCfg(enabled=True)))
    assert list(on) == [1] * 22 + [0], "开着必须在暴跌那根触发，否则本测试没有分辨力"
    pd.testing.assert_series_equal(target_positions(df, strat, OFF),
                                   strat.generate_positions(df))


def test_the_default_overlays_config_disables_everything():
    """无 overlays 段（= OverlaysCfg()）就是全关：旧 config 与既有测试原样可用。"""
    assert OverlaysCfg().atr_stop.enabled is False


def test_the_output_contract_is_int_0_1_on_the_same_index():
    """契约与 generate_positions 一致：与 df.index 对齐的 int {0,1}。
    bool 会让下游 shift(1) 变 object，float 会让引擎的 int(pos) 静默取整。"""
    df = bars(BLOCKED)
    pos = target_positions(df, Fixed([0, 0] + [1] * 9), stop())
    assert pd.api.types.is_integer_dtype(pos)
    assert set(pos.unique()) <= {0, 1}
    assert pos.index.equals(df.index)


# ============================================================ 三入口收拢（设计 §2.1）


def _source_calls(path: Path) -> list[tuple[str, int]]:
    """文件里所有函数调用的 (被调名, 行号)。用 AST 而不是文本查找：
    注释与文档串里提到函数名不该算"调用"，而 `x.generate_positions(df)` 这种
    属性调用也要认得出来。"""
    out: list[tuple[str, int]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name:
                out.append((name, node.lineno))
    return out


ENTRY_FILES = sorted((ROOT / "scripts").glob("*.py")) + \
    sorted((ROOT / "src" / "quant" / "signal").glob("*.py"))


def test_no_entry_point_calls_generate_positions_directly():
    """源码级断言：scripts/ 与 src/quant/signal/ 下不许再出现 generate_positions( 调用。

    此前三处各自直调（run_backtest.py、signal/market_scan.py、signal/scan.py）。
    叠加层若逐处接入，漏掉任何一处就是"扫描说买、回测按另一套算、每日信号第三套"，
    三边都不报错。行为测试查不出这种分叉（漏接的那处照样输出一串合法仓位），
    所以这里钉源码。
    """
    offenders = [(p.name, ln) for p in ENTRY_FILES
                 for name, ln in _source_calls(p) if name == "generate_positions"]
    assert offenders == [], (
        f"入口层仍在直调 generate_positions：{offenders}。"
        f"目标仓位只有一个出口——quant.strategy.pipeline.target_positions")


@pytest.mark.parametrize("rel", ["scripts/run_backtest.py",
                                 "src/quant/signal/scan.py",
                                 "src/quant/signal/market_scan.py"])
def test_every_entry_point_goes_through_the_pipeline(rel):
    """反向断言：三个入口都真的调了 target_positions。
    只有上面那条否定断言时，把整段仓位计算删掉也能"通过"。"""
    names = {name for name, _ln in _source_calls(ROOT / rel)}
    assert "target_positions" in names, f"{rel} 没有经过 pipeline.target_positions"


def _padded(adj_closes, base, factors=None):
    """把手算序列垫到 MIN_BARS 根之上（全市场扫描的暖机闸门要求 ≥130 根），
    返回 (df, 基础信号列表)。

    前缀在 首值/首值+1 之间锯齿：TR = 1 而不是 0——全平会让 ATR = 0，
    于是"任何一分钱的回撤都触发止损"，测出来的结论与真实股票毫无关系。
    前缀最后一根**等于手算序列的首根**，因此手算窗口内的 TR 一根没变；
    前缀期间基础信号恒为 0，不可能提前止损。
    """
    n_pad = MIN_BARS + 4 - len(adj_closes)
    prefix = [adj_closes[0] + (i % 2) for i in range(n_pad)]
    prefix[-1] = adj_closes[0]
    pad_factors = [1.0] * n_pad
    return (bars(prefix + list(adj_closes),
                 factors=None if factors is None else pad_factors + list(factors)),
            [0] * n_pad + list(base))


# (用例名, 收盘价路径, 基础信号, pipeline 末两根期望)
CONSISTENCY_CASES = [
    ("止损当根", TRIGGERED, ENTER_AT_2, (1, 0)),
    ("回撤没到阈值", NOT_TRIGGERED, ENTER_AT_2, (1, 1)),
    ("止损后封锁中", BLOCKED, [0, 0] + [1] * 9, (0, 0)),
    ("基础信号新入场", BLOCKED, [0, 0, 1, 1, 1, 1, 1, 1, 1, 0, 1], (0, 1)),
]


@pytest.mark.parametrize("why, closes, base, tail", CONSISTENCY_CASES)
def test_three_entry_points_agree_on_the_same_df_and_config(why, closes, base, tail):
    """同一个 df、同一个策略、同一份 overlays 配置 → 三个入口的目标仓位逐位相同。

    这是"收拢"的行为侧证明。特别看「止损当根」一行：基础信号末两根是 (1,1)，
    没有任何卖出信号；只有走 pipeline 的入口才会报出这次 SELL——
    每日信号从此开始出现止损触发的卖出，正是本版给用户补的真缺口。
    """
    df, base = _padded(closes, base)
    strat = Fixed(base)
    ov = stop()
    want = target_positions(df, strat, ov)
    assert (int(want.iloc[-2]), int(want.iloc[-1])) == tail, "手算的末两根先对上"

    # 入口一：回测
    pd.testing.assert_series_equal(
        run_backtest.strategy_positions(strat, {"600000": df}, ov)["600000"], want)

    # 入口二：每日信号（固定池，BUY/SELL 双向）
    sigs = scan({"600000": df}, [strat], overlays=ov)
    if tail[0] == tail[1]:
        assert sigs == []
    else:
        assert [s["action"] for s in sigs] == ["BUY" if tail[1] > tail[0] else "SELL"]

    # 入口三：全市场扫描（只报新 BUY）
    got, skip = classify_and_scan(df, [strat], EXPECTED, 5e7, overlays=ov)
    assert skip is None, f"这条用例不该被跳过判定拦下: {skip}"
    assert bool(got) is (tail == (0, 1))


def test_the_stop_overlay_neither_invents_nor_erases_a_fresh_buy():
    """全市场扫描的"新 BUY"判据不受 ATR 止损影响，这是语义，不是巧合：
    overlay 只把 1 压成 0，而入场当根不可能被止损（peak = 当根收盘）——
    于是 0→1 既不会被抹掉，也不会被凭空造出来。

    钉住它的用处：M3 的趋势过滤**会**改变 BUY 判据（gate 挡住入场），
    届时这条必须显式改动，而不是悄悄变了没人发现。
    """
    for closes, base in ((TRIGGERED, ENTER_AT_2),
                         (BLOCKED, [0, 0, 1, 1, 1, 1, 1, 1, 1, 0, 1])):
        df, base = _padded(closes, base)
        on, _ = classify_and_scan(df, [Fixed(base)], EXPECTED, 5e7, overlays=stop())
        off, _ = classify_and_scan(df, [Fixed(base)], EXPECTED, 5e7, overlays=OFF)
        assert [s["strategy"] for s in on] == [s["strategy"] for s in off]
