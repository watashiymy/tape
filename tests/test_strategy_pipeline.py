# tests/test_strategy_pipeline.py — v0.4.0 M2：信号流水线收拢 + ATR 追踪止损（设计 §2）
#
# 两件事在这里被钉住：
#   1. **收拢**：回测/信号跟踪/全市场扫描三个入口的目标仓位只有一个出口
#      （quant.strategy.pipeline.target_positions）。此前三处各自调
#      strat.generate_positions(df)，叠加层只要漏接一处，就会出现"扫描说买、
#      回测按另一套规则算、信号跟踪又是第三套"——静默分叉，且三边都不报错。
#   2. **ATR 追踪止损的语义**：阈值手算、止损后不得回补、暖机期不装算得出来、
#      截断重放无未来函数、关闭时是恒等变换。
import ast
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from quant.config import AtrStopCfg, OverlaysCfg, TrendFilterCfg
from quant.indicators import atr, ma
from quant.signal.market_scan import MIN_BARS, classify_and_scan
from quant.signal.scan import scan
from quant.strategy.base import Strategy
from quant.strategy.donchian import Donchian
from quant.strategy.ma_cross import MaCross
from quant.strategy.pipeline import atr_trailing_stop, target_positions, trend_gate
from quant.strategy.tsmom import TSMomentum
from tests.conftest import make_bars

ROOT = Path(__file__).resolve().parent.parent


def _load_script(stem: str):
    spec = importlib.util.spec_from_file_location(
        f"{stem}_m2", ROOT / "scripts" / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


run_backtest = _load_script("run_backtest")
run_daily_signal = _load_script("run_daily_signal")

EXPECTED = pd.Timestamp("2024-06-28").date()   # 周五，bdate_range(end=...) 的最后一根

OFF = OverlaysCfg()                            # 无 overlays 段 = 全部禁用


def stop(n: int = 4, k: float = 3.0) -> OverlaysCfg:
    return OverlaysCfg(atr_stop=AtrStopCfg(enabled=True, n=n, k=k))


def gate(n: int = 4) -> OverlaysCfg:
    """只开趋势过滤（v0.4.0 M3）。窗口取 4 而不是默认 200：手算表要能写下来。"""
    return OverlaysCfg(trend_filter=TrendFilterCfg(enabled=True, n=n))


def both(gate_n: int = 4, stop_n: int = 4, k: float = 3.0) -> OverlaysCfg:
    return OverlaysCfg(trend_filter=TrendFilterCfg(enabled=True, n=gate_n),
                       atr_stop=AtrStopCfg(enabled=True, n=stop_n, k=k))


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


#: 二次入场（idx10，入场价 150）之后每根跌 30，用来钉"回撤恰好等于阈值不触发"这条边界
REENTRY = BLOCKED + [120.0, 90.0, 60.0, 30.0]
REENTRY_BASE = [0, 0, 1, 1, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1]


def test_the_drawdown_threshold_is_a_strict_less_than():
    """"回撤 == 阈值不触发"这条边界（判据是 `<` 而不是 `<=`）。

    idx10 以 150 入场，随后每根跌 30（TR=30 → ATR(4)=30，阈值 3×30 = 90）：
      idx13: c=60 → 回撤 = 150−60 = 90，**恰好等于**阈值 → 不触发
      idx14: c=30 → 回撤 = 120 > 90 → 触发
    写成 <= 会让止损整体提前一档（每一笔都早认输一根）。

    注意本条**分不开** peak 的两种口径：二次入场价 150 高于上一轮的旧 peak 120，
    于是"新 peak"与"跨轮沿用的旧 peak"逐位一致（max(120, 150) == 150）。
    那条语义由下面那条（入场价落在旧 peak **之下**）单独钉。
    """
    pos = target_positions(bars(REENTRY), Fixed(REENTRY_BASE), stop())
    assert list(pos) == [0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 1, 1, 1, 1, 0]


#: 二次入场价 60 落在**旧 peak 120 之下**——两种 peak 口径只有这样才分得开。
#: idx6 打平（TR=0）让基础信号先离场，idx7 以 60 重新入场，idx8 跌到 40。
RE_LOW = TRIGGERED + [27.0, 60.0, 40.0]
RE_LOW_BASE = [0, 0, 1, 1, 1, 1, 0, 1, 1]


def test_re_entry_below_the_old_peak_starts_a_brand_new_peak():
    """peak 从**新入场那根**起算，绝不跨轮沿用上一轮的高点。

    这条是全套件里唯一分得开两种 peak 口径的用例，所以它决定每一笔的认输点：
    一旦 peak 被写成"全局滚动最高"或"止损后仍沿用"，止损线会钉在早已作废的旧高点上，
    新一轮刚入场就带着一大截"回撤"，回测里只表现为"止损变紧了一点"，无人报错。

    手算（bars() 里 TR = |Δadj_close|，首根退化为 high−low = 0）：
      idx      0     1     2      3     4     5      6      7      8
      close   66    93   120     89    58    27     27     60     40
      TR       0    27    27     31    31    31      0     33     20
      ATR(4) NaN   NaN   NaN  21.25  29.0  30.0  23.25  23.75  21.00
    第一轮：idx2 以 120 入场；idx5 回撤 120−27 = 93 > 3×30 = 90 → 止损（既有用例已钉）。
    idx6 基础信号回 0（封锁作废），idx7 基础信号 0→1 → 以 60 **重新入场**：
      idx7: 阈值 3×23.75 = 71.25。新 peak 口径回撤 = 60−60 = 0 → 不触发；
            旧 peak 口径回撤 = 120−60 = 60 < 71.25 → 也不触发（两种口径这里同分，
            所以判据必须看下一根）。
      idx8: 阈值 3×21.0 = 63。新 peak 口径回撤 = 60−40 = 20 < 63 → **不触发**；
            旧 peak 口径回撤 = 120−40 = 80 > 63 → 会凭空止损一次。
    """
    pos = target_positions(bars(RE_LOW), Fixed(RE_LOW_BASE), stop())
    assert list(pos) == [0, 0, 1, 1, 1, 0, 0, 1, 1]
    assert int(pos.iloc[-1]) == 1, (
        "末根被止掉 = peak 沿用了上一轮的旧高点 120（正确口径是新入场价 60，"
        "回撤 20 远不到阈值 63）")


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


# ============================================================ 趋势过滤（M3 设计 §3.1）
#
# gate = adj_close > MA(n)，最终仓位 = base AND gate（Faber 风格）。语义是"价格在长期
# 均线下方时不持有多头"：**既挡入场也强制出场**。下面每个数字都手算，MA 用 n=4
# 的小窗口（默认 200 根写不下来，而窗口长度不改变语义）。
#
#   idx        0     1     2     3      4     5      6      7      8
#   adj_close 10    20    30    40     20    50     30     34     38
#   MA(4)    NaN   NaN   NaN  25.0   27.5  35.0   35.0   33.5   38.0
#   gate       F     F     F     T      F     T      F      T      F
#            暖机 ——————————→   ↑高于  ↑跌破  ↑重回  ↑跌破  ↑高于  ↑**恰好等于**
# MA(4) 逐根：idx3 (10+20+30+40)/4=25、idx4 (20+30+40+20)/4=27.5、
#            idx5 (30+40+20+50)/4=35、idx6 (40+20+50+30)/4=35、
#            idx7 (20+50+30+34)/4=33.5、idx8 (50+30+34+38)/4=38。
GATE_CLOSES = [10.0, 20.0, 30.0, 40.0, 20.0, 50.0, 30.0, 34.0, 38.0]
GATE_WANT = [0, 0, 0, 1, 0, 1, 0, 1, 0]
#: idx4 起 10 送 10 除权（因子 1→2）：raw 价腰斩，均线比较不再是尺度不变的
#: （窗口跨除权日时 raw 的四根价来自两个尺度），"gate 误用原始价"才测得出来。
GATE_FACTORS = [1.0] * 4 + [2.0] * 5


def test_the_trend_gate_is_hand_computable():
    """gate 就是"收盘价高于 MA(n)"这一句，逐根手算钉住（表见上）。

    暖机期是 MA 未成形的前 n−1 根（与 MaCross 的暖机口径一致：rolling(n) 的前
    n−1 根是 NaN）。判据是**严格 >**：idx8 收盘与 MA 逐分不差 → 不持有。
    """
    df = bars(GATE_CLOSES)
    m = ma(df["adj_close"], 4)
    assert m.iloc[:3].isna().all(), "MA(4) 前 3 根必须是 NaN，否则下面的暖机断言没意义"
    assert list(m.iloc[3:]) == [25.0, 27.5, 35.0, 35.0, 33.5, 38.0]
    assert df["adj_close"].iloc[8] == m.iloc[8] == 38.0, "手算前提：末根收盘恰好等于 MA"
    g = trend_gate(df, 4)
    assert list(g.astype(int)) == GATE_WANT


def test_the_final_position_is_the_base_signal_and_the_gate():
    """最终仓位 = base AND gate：两侧都要能把 1 压成 0。

    base 在 idx3/idx7 是 0 而 gate 是 True（AND 由 base 侧压掉——写成 `gate` 直接
    当仓位就会凭空造出两次入场），idx4/idx6 反过来（AND 由 gate 侧压掉）。
    """
    base = [1, 1, 1, 0, 1, 1, 1, 0, 1]
    pos = target_positions(bars(GATE_CLOSES), Fixed(base), gate())
    assert list(pos) == [0, 0, 0, 0, 0, 1, 0, 0, 0]
    assert list(trend_gate(bars(GATE_CLOSES), 4).astype(int)) == GATE_WANT, \
        "gate 本身在 idx3/idx7 是 True，被 base 压掉才是 AND 的证据"


def test_a_base_signal_that_never_leaves_is_gated_bar_by_bar():
    """base 恒为 1 时最终仓位**逐位等于** gate——过滤层自己说了算的那一面。"""
    pos = target_positions(bars(GATE_CLOSES), Fixed([1] * 9), gate())
    assert list(pos) == GATE_WANT


#: 连涨到 50 之后一根跌回 20：base 一直是 1，只有 gate 会让它离场
FORCED_EXIT = [10.0, 20.0, 30.0, 40.0, 50.0, 20.0]

#: 横盘 100 之后末根跌到 60，基础策略恰在末根给出一次干净的 0→1 入场。
#: 末根 MA(4) = (100+100+100+60)/4 = 90 > 收盘 60 → gate 挡住这次入场。
#: 这条序列是"趋势过滤会改变全市场扫描的新 BUY 判据"（设计 §2.1 末段）的载体。
GATE_BLOCK = [100.0, 100.0, 100.0, 100.0, 60.0]
GATE_BLOCK_BASE = [0, 0, 0, 0, 1]


def test_the_gate_forces_an_exit_when_the_price_drops_below_the_ma():
    """"跌破长期均线就离场"是这层过滤的一半价值（另一半是挡入场）。

    手算：idx3 MA=(10+20+30+40)/4=25 → 40>25 持有；idx4 MA=(20+30+40+50)/4=35 →
    50>35 持有；idx5 MA=(30+40+50+20)/4=35 → 20<35 → **离场**。
    基础信号全程是 1，这次卖出只可能来自 gate——写成"只挡入场、不管出场"
    （例如只在 base 的 0→1 那根查 gate）时，末根会留着一个 1，而回测只表现为
    "回撤深了一点"，没有任何报错。
    """
    pos = target_positions(bars(FORCED_EXIT), Fixed([1] * 6), gate())
    assert list(pos) == [0, 0, 0, 1, 1, 0]


def test_the_warmup_gate_holds_nothing_even_in_a_screaming_uptrend():
    """MA 还没成形就不持有——算不出来就不装算得出来（与 ATR 暖机同一纪律）。

    前缀刻意取连涨（10→40）：横盘时"暖机为 False"与"暖机照常判定"都给 False，
    断言会退化成永真。这里若把 NaN 当 0（fillna）或用 min_periods=1，
    idx1 就会变成持有（MA(10,20)=15 < 20）。
    """
    df = bars([10.0, 20.0, 30.0, 40.0])
    assert list(trend_gate(df, 4).astype(int)) == [0, 0, 0, 1]
    assert ma(df["adj_close"], 4).iloc[:3].isna().all()
    pos = target_positions(df, Fixed([1, 1, 1, 1]), gate())
    assert list(pos) == [0, 0, 0, 1], "暖机三根必须空仓，第 4 根 MA 成形后才谈得上持有"


def test_the_gate_reads_the_adjusted_close_not_the_raw_close():
    """gate 的两侧（当日价与均线）都用后复权价。

    因子在 idx4 从 1 跳到 2（10 送 10 除权），raw 价腰斩。均线比较本身是尺度不变的，
    但**窗口跨除权日**时 raw 的四根价来自两个尺度：手算 idx5 的 raw 口径
    MA=(30+40+10+25)/4=26.25 > 收盘 25 → 误判为"在均线下方"，凭空少一次持有。
    """
    df = bars(GATE_CLOSES, factors=GATE_FACTORS)
    assert df["close"].iloc[4] == pytest.approx(10.0), "raw 确实腰斩了"
    assert list(trend_gate(df, 4).astype(int)) == GATE_WANT
    raw_gate = (df["close"] > ma(df["close"], 4)).astype(int)
    assert list(raw_gate) != GATE_WANT, "原始价口径必须给出不同答案，否则测不出误用"


def test_a_disabled_trend_filter_is_the_identity_transform():
    """关掉过滤 → 逐位回到基础信号（含 dtype 与 name）。

    与 atr 那条恒等测试同一理由（向后兼容），但这条要**自证有分辨力**：
    先证明同一段行情开着过滤确实会改变结果，恒等才是"开关被尊重"的功劳。
    """
    df = bars(GATE_CLOSES)
    strat = Fixed([1] * 9)
    assert list(target_positions(df, strat, gate())) == GATE_WANT != [1] * 9
    pd.testing.assert_series_equal(target_positions(df, strat, OFF),
                                   strat.generate_positions(df))


# ---------- 顺序：先 gate 后 atr_stop（本里程碑最容易写反的一条）----------
#
# 手算序列（gate n=4、atr n=4、k=3.0；bars() 里 TR = |Δadj_close|，首根退化为 0）：
#   idx        0     1     2     3     4     5     6      7      8
#   adj      100   102   100   102    60    58    59     95    130
#   TR         0     2     2     2    42     2     1     36     35
#   ATR(4)   NaN   NaN   NaN   1.5  12.0  12.0 11.75  20.25   18.5
#   MA(4)    NaN   NaN   NaN 101.0  91.0  80.0 69.75   68.0   85.5
#   gate       F     F     F     T     F     F     F      T      T
#
# 先 gate 后 atr（正确）：atr 收到的输入是 [0,0,0,1,0,0,0,1,1]——
#   idx3 入场（peak=102，回撤 0）；idx4 gate 关门 → 输入 0 → 离场且封锁作废；
#   idx7 gate 重新放行 → 以 95 **重新入场**（peak=95，回撤 0）；idx8 继续持有。
#   → [0,0,0,1,0,0,0,1,1]
# 先 atr 后 gate（错误）：atr 收到的是恒为 1 的 base——
#   idx0 就入场，peak 抬到 102；idx4 回撤 102−60=42 > 3×12=36 → **止损并封锁**；
#   而封锁只解除于 base 回 0，base 永远是 1 → 此后全 0，再 AND gate 还是全 0。
#   → [0,0,0,1,0,0,0,0,0]
# 差别落在 idx7/idx8：**趋势恢复后的那次入场被静默吞掉**。
ORDER = [100.0, 102.0, 100.0, 102.0, 60.0, 58.0, 59.0, 95.0, 130.0]
ORDER_WANT = [0, 0, 0, 1, 0, 0, 0, 1, 1]
ORDER_IF_REVERSED = [0, 0, 0, 1, 0, 0, 0, 0, 0]


def test_the_gate_is_applied_before_the_stop_not_after():
    """顺序是语义：先决定"这个环境能不能持有"，再管"持有之后何时认输"（设计 §3.1）。

    反过来（先算止损、再 AND gate）时，止损的状态机会把一次**本来不该持有**的下跌
    当成一笔真实持仓来认输，而"止损后保持空仓直到基础策略新的 0→1"这条封锁只看
    base——base 恒为 1 的策略（均线仍多头排列）从此永久被拉黑，趋势恢复后的入场
    再也不会出现。两种顺序都输出一串合法仓位，没有任何一处报错。
    """
    df = bars(ORDER)
    strat = Fixed([1] * len(ORDER))
    got = target_positions(df, strat, both())
    assert list(got) == ORDER_WANT

    # 反序的结果在这里**真算一遍**（用同两个纯函数），证明这条测试分得开顺序：
    # 不同才说明上面那个断言钉住的是顺序，而不是"两种顺序恰好一样"。
    g = trend_gate(df, 4)
    reversed_order = (atr_trailing_stop(df, strat.generate_positions(df), n=4, k=3.0)
                      .astype(bool) & g).astype(int)
    assert list(reversed_order) == ORDER_IF_REVERSED
    assert list(got) != list(reversed_order), "顺序反了结果必须不同，否则这条测试是空的"


def test_the_gate_blocking_an_entry_leaves_the_stop_with_nothing_to_trigger_on():
    """同一段行情：**只开** gate 与**两个都开**逐位相同。

    因为 gate 挡住的那段下跌根本没进过仓，止损"无从触发"。这条与上一条互为犄角：
    上一条证明顺序反了会多一次止损（并永久封锁），这条证明正确顺序下止损在这段
    行情里一次都不该说话——如果 both() 的结果比 gate() 少了一个 1，
    就说明止损在替 gate 已经处理掉的下跌重复认输。
    """
    df = bars(ORDER)
    strat = Fixed([1] * len(ORDER))
    assert list(target_positions(df, strat, gate())) == ORDER_WANT
    assert list(target_positions(df, strat, both())) == ORDER_WANT


def test_the_stop_still_works_on_top_of_an_open_gate():
    """反向：gate 一路放行时止损照常触发——两层叠加不是互相抵消。

    用 STEP_UP（22 根每根涨 10，末根跌到 260；见文件末尾那段手算）：
      末根 MA(20) = (130+140+…+310+260)/20 = 4440/20 = **222** → 260 > 222，gate 放行；
      末根 ATR(20) = (19×10+50)/20 = 12 → 阈值 36，回撤 310−260 = 50 > 36 → 止损。
    所以末根那个 0 只能来自止损：若把两层写成"任一层挡住就归零"之外的什么东西
    （例如后一层覆盖前一层的结论），这里会留下一个 1。
    """
    df = bars(STEP_UP)
    strat = Fixed([1] * len(STEP_UP))
    assert float(ma(df["adj_close"], 20).iloc[-1]) == pytest.approx(222.0)
    assert int(trend_gate(df, 20).iloc[-1]) == 1, "末根必须在均线上方（gate 放行）"
    pos = target_positions(df, strat, both(gate_n=20, stop_n=20, k=3.0))
    assert int(pos.iloc[-2]) == 1
    assert int(pos.iloc[-1]) == 0, "gate 放行，末根这次 0 只能来自 ATR 止损"


# ---------- 三策略 × overlay 开/关 的组合冒烟（设计 §3.3）----------

#: 涨 → 跌 → 涨 → 跌，35 根：三个策略在这段里都真的有进有出（下面有断言钉着）
COMBO = ([100.0 + 2.0 * i for i in range(12)]          # 100 → 122
         + [120.0 - 2.0 * i for i in range(8)]         # 120 → 106
         + [108.0 + 2.4 * i for i in range(10)]        # 108 → 129.6
         + [128.0 - 4.0 * i for i in range(5)])        # 128 → 112

COMBO_STRATEGIES = [
    MaCross(fast=2, slow=4),
    Donchian(entry_n=3, exit_n=2, amount_n=3, amount_ratio=0.5),
    TSMomentum(lookback=3),
]
COMBO_OVERLAYS = [("全关", OFF), ("只开趋势过滤", gate(5)),
                  ("只开止损", stop(4, 3.0)), ("两个都开", both(5, 4, 3.0))]


@pytest.mark.parametrize("why, ov", COMBO_OVERLAYS)
@pytest.mark.parametrize("strat", COMBO_STRATEGIES, ids=lambda s: s.name)
def test_every_strategy_overlay_combination_keeps_the_output_contract(why, ov, strat):
    """三策略 × 四种叠加层组合：契约（int {0,1}、同 index）+ **叠加层只减不增**。

    "只减不增"是这两层 overlay 共同的语义（gate 是 AND、止损只把 1 压成 0）：
    任何一处写成"或"、或把 gate 直接当仓位用，都会凭空造出基础策略从没给过的入场，
    而回测照常完成——多出来的那些交易看起来和真信号一模一样。
    """
    df = bars(COMBO)
    base = strat.generate_positions(df)
    assert 0 < int(base.sum()) < len(df), f"{strat.name} 在这段行情里必须有进有出"
    pos = target_positions(df, strat, ov)
    assert pd.api.types.is_integer_dtype(pos) and set(pos.unique()) <= {0, 1}
    assert pos.index.equals(df.index)
    assert (pos <= base).all(), f"{why}：叠加层把 0 变成了 1（只该只减不增）"


@pytest.mark.parametrize("why, ov", COMBO_OVERLAYS)
@pytest.mark.parametrize("strat", COMBO_STRATEGIES, ids=lambda s: s.name)
def test_every_strategy_overlay_combination_survives_a_truncated_replay(why, ov, strat):
    """截断重放：任何策略 × 任何叠加层组合都不许偷看未来。
    这是本项目对每个信号生成器的固定体检，叠加层加进来之后一样要过。"""
    df = bars(COMBO)
    full = list(target_positions(df, strat, ov))
    for cut in range(1, len(COMBO) + 1):
        trunc = target_positions(bars(COMBO[:cut]), strat, ov)
        assert list(trunc) == full[:cut], f"{strat.name}/{why} 截断到 {cut} 根后历史被改写"


@pytest.mark.parametrize("strat", COMBO_STRATEGIES, ids=lambda s: s.name)
def test_the_trend_filter_actually_changes_every_strategy(strat):
    """反向断言：这三个策略在这段行情里都**确实**被趋势过滤改变了结果。

    没有这条，上面那两条"契约 + 只减不增"对一个把 gate 整段忽略的实现同样全绿。
    """
    df = bars(COMBO)
    base = list(strat.generate_positions(df))
    assert list(target_positions(df, strat, gate(5))) != base, \
        f"{strat.name} 的结果没被趋势过滤改变，这组冒烟测试对它没有分辨力"


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
    叠加层若逐处接入，漏掉任何一处就是"扫描说买、回测按另一套算、信号跟踪第三套"，
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


# ---------- 传的是**配置里那份** overlays（上面两条断言管不到实参）----------
#
# "漏接"有两种形态：一种是根本不走 pipeline（上面两条钉住了），另一种是走了 pipeline
# 但把一个全关的 OverlaysCfg() 递进去——配置里写着 atr_stop 开着，实际按无叠加层跑，
# 三个入口各自都输出一串合法仓位，零告警。第二种形态完全合法（overlays 是必填参数，
# 填什么都是填了），所以必须钉到**实参**上。

#: 哪些函数按位置收 overlays（第几个位置参数）；其余一律走关键字。
_OVERLAYS_POSITION = {"target_positions": 2, "strategy_positions": 2}

#: 允许出现的两种实参形态。`settings.overlays` = load_settings 读出来的那份；
#: `overlays` = 本函数自己的同名参数往下转发。除此之外的任何表达式（尤其是就地
#: 构造的 OverlaysCfg(...)）都意味着这一处跑的规则与配置文件无关。
_ALLOWED_OVERLAYS_ARGS = {"settings.overlays", "overlays"}


def _overlays_arguments(path: Path) -> list[tuple[str, int]]:
    """文件里每一处"把叠加层交出去"的实参源码 + 行号。

    用 AST 而不是文本匹配：实参是表达式，`settings.overlays` 与 `OverlaysCfg()`
    只有解析出来才分得开，而这两者的差别正是"按配置跑"与"按默认跑"的差别。
    """
    out: list[tuple[str, int]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "overlays":
                out.append((ast.unparse(kw.value), kw.value.lineno))
        name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        pos = _OVERLAYS_POSITION.get(name)
        if pos is not None and len(node.args) > pos:
            out.append((ast.unparse(node.args[pos]), node.args[pos].lineno))
    return out


@pytest.mark.parametrize("rel", ["scripts/run_backtest.py",
                                 "scripts/run_daily_signal.py",
                                 "scripts/run_market_scan.py"])
def test_every_script_passes_the_overlays_from_the_loaded_settings(rel):
    """三个可执行入口都必须把 `settings.overlays` 递下去。

    这三个脚本是唯一 load_settings 的地方，也就是唯一有资格决定"这轮按什么规则跑"
    的地方。换成 OverlaysCfg() 就是"配置写着开、实际关着"——回测/扫描/信号跟踪
    照常跑完、exit 0，而每一笔的认输点全变了。
    """
    got = _overlays_arguments(ROOT / rel)
    assert got, f"{rel} 一处叠加层实参都没有——它没在把配置递给 pipeline"
    assert "settings.overlays" in [src for src, _ln in got], (
        f"{rel} 没有把配置里的叠加层递下去，实际递的是 {got}")


def test_no_entry_point_conjures_its_own_overlays():
    """反向断言：入口层不许就地造 overlays。

    上面那条只要求"settings.overlays 出现过"；本条管住每一处实参，
    杜绝"一处按配置跑、另一处按默认跑"这种半漏接（那比全漏接更难发现）。
    """
    bad = [(p.name, src, ln) for p in ENTRY_FILES
           for src, ln in _overlays_arguments(p)
           if src not in _ALLOWED_OVERLAYS_ARGS]
    assert bad == [], (
        f"入口层就地构造了叠加层配置：{bad}。只允许 {sorted(_ALLOWED_OVERLAYS_ARGS)}——"
        f"要么是配置里那份，要么是同名参数的转发")


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
    信号跟踪从此开始出现止损触发的卖出，正是本版给用户补的真缺口。
    """
    df, base = _padded(closes, base)
    strat = Fixed(base)
    ov = stop()
    want = target_positions(df, strat, ov)
    assert (int(want.iloc[-2]), int(want.iloc[-1])) == tail, "手算的末两根先对上"

    # 入口一：回测
    pd.testing.assert_series_equal(
        run_backtest.strategy_positions(strat, {"600000": df}, ov)["600000"], want)

    # 入口二：信号跟踪（固定池，BUY/SELL 双向）
    sigs = scan({"600000": df}, [strat], overlays=ov)
    if tail[0] == tail[1]:
        assert sigs == []
    else:
        assert [s["action"] for s in sigs] == ["BUY" if tail[1] > tail[0] else "SELL"]

    # 入口三：全市场扫描（只报新 BUY）
    got, skip = classify_and_scan(df, [strat], EXPECTED, 5e7, overlays=ov)
    assert skip is None, f"这条用例不该被跳过判定拦下: {skip}"
    assert bool(got) is (tail == (0, 1))


# (用例名, 收盘价路径, 基础信号, 叠加层, pipeline 末两根期望)
GATE_CONSISTENCY_CASES = [
    ("趋势过滤挡住入场", GATE_BLOCK, GATE_BLOCK_BASE, gate(), (0, 0)),
    ("同一序列关掉过滤就放行", GATE_BLOCK, GATE_BLOCK_BASE, OFF, (0, 1)),
    ("跌破均线强制离场", FORCED_EXIT, [1] * len(FORCED_EXIT), gate(), (1, 0)),
    ("两层叠加：gate 重新放行才入场", ORDER[:8], [1] * 8, both(), (0, 1)),
]


@pytest.mark.parametrize("why, closes, base, ov, tail", GATE_CONSISTENCY_CASES)
def test_three_entry_points_agree_with_the_trend_filter_too(why, closes, base, ov, tail):
    """趋势过滤开启后，三个入口仍然逐位相同（收拢的行为侧证明，覆盖到 M3 的新叠加层）。

    看「跌破均线强制离场」一行：基础信号末两根是 (1,1)，没有任何卖出信号；
    这条 SELL 只可能来自 gate。看「趋势过滤挡住入场」一行：基础信号给了一次干净的
    0→1，而三个入口都不该报出这次 BUY——**这正是 M2 时办不到的那条行为断言**
    （见 §2.1：atr_stop 对"新 BUY"恒等，趋势过滤打破了它）。
    """
    df, base = _padded(closes, base)
    strat = Fixed(base)
    want = target_positions(df, strat, ov)
    assert (int(want.iloc[-2]), int(want.iloc[-1])) == tail, "手算的末两根先对上"

    pd.testing.assert_series_equal(
        run_backtest.strategy_positions(strat, {"600000": df}, ov)["600000"], want)

    sigs = scan({"600000": df}, [strat], overlays=ov)
    if tail[0] == tail[1]:
        assert sigs == []
    else:
        assert [s["action"] for s in sigs] == ["BUY" if tail[1] > tail[0] else "SELL"]

    got, skip = classify_and_scan(df, [strat], EXPECTED, 5e7, overlays=ov)
    assert skip is None, f"这条用例不该被跳过判定拦下: {skip}"
    assert bool(got) is (tail == (0, 1))


# ---------- 端到端：配置说开，跑出来就得是开着的那套 ----------
#
# 上面几条是源码级的。源码级断言的软肋是它只认表达式长相，不认运行时行为；
# 所以再补一条真跑 main() 的：临时 config 里 atr_stop 开着 → CSV 里必须出现这次
# 止损 SELL；同一段行情把开关关掉 → 一条信号都没有。两次跑的唯一差别就是配置里
# 那个 enabled，于是"配置被尊重"这件事有了行为侧的证据。
#
# 手算（rise=10 的稳步上涨 22 根 100→310，末根跌到 260）：
#   TR: idx0 = 0（首根退化为 high−low），idx1..21 = 10，idx22 = 50
#   ATR(20)@idx22 = (19×10 + 50)/20 = 12 → 阈值 3×12 = 36
#   peak = 310（idx21），回撤 = 310 − 260 = 50 > 36 → 末根止损
#   基础信号（ma_cross 5/10）末根仍是 1：MA5 = (280+290+300+310+260)/5 = 288，
#   MA10 = (230+…+310+260)/10 = 269 → 288 > 269。**没有任何基础卖出信号**，
#   这条 SELL 只可能来自止损；参数选得很紧：跌幅再大一点（>3×rise=30 那档往上到
#   6×rise）才既触发止损又不把快线打到慢线之下，所以 50 这个数字不是随手写的。
STEP_UP = [100.0 + 10.0 * i for i in range(22)] + [260.0]

_DAILY_CFG = """
universe: ["600000"]
benchmark: "000300"
backtest: {start: "2024-01-01", capital: 5000000}
costs:
  commission_rate: 0.00025
  commission_min: 5.0
  stamp_tax: [{rate: 0.0005}]
  slippage: 0.001
strategies: {ma_cross: {fast: 5, slow: 10}}
overlays:
  atr_stop: {enabled: %s, n: 20, k: 3.0}
"""


def _run_daily_signal(tmp_path, monkeypatch, enabled: str) -> pd.DataFrame:
    """在 tmp_path 里跑一次 run_daily_signal.main()，返回它写出的信号 CSV。

    联网的两件事（交易日历、取行情）换成假的；其余全部走真代码——尤其是
    load_settings 与 scan(..., overlays=...) 那条线，这正是要钉的东西。
    cwd 换到 tmp_path：脚本里的 config/settings.yaml、data/cache、output/signals
    都是相对路径，绝不可能碰到仓库里用户自己的产物。
    """
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "settings.yaml").write_text(_DAILY_CFG % enabled,
                                                       encoding="utf-8")
    df = bars(STEP_UP)

    class FakeProvider:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get_trade_calendar(self, start, end): return [EXPECTED]

    class FakeService:
        def __init__(self, provider, cache): pass
        def get_bars(self, symbol, start, **kw): return df.copy(), []

    monkeypatch.setattr(run_daily_signal, "BaostockProvider", FakeProvider)
    monkeypatch.setattr(run_daily_signal, "DataService", FakeService)
    monkeypatch.chdir(tmp_path)
    # v0.5.0 起脚本带 argparse（--date）：不给 sys.argv 一个干净值，它会把 pytest
    # 自己的命令行当参数解析（与 tests/test_run_market_scan.py 的 _run_scan 同一做法）
    monkeypatch.setattr("sys.argv", ["run_daily_signal.py"])
    run_daily_signal.main()
    out = tmp_path / "output" / "signals" / f"{EXPECTED}.csv"
    assert out.exists(), f"脚本没写出 {out}"
    return pd.read_csv(out)


def test_run_daily_signal_actually_runs_the_stop_its_config_asks_for(tmp_path, monkeypatch):
    """配置里 atr_stop 开着 → 信号跟踪必须报出这次止损 SELL。

    这是本版给用户补的真缺口：此前系统等权满仓、没有任何出场保护，而基础信号
    （均线仍多头排列）永远不会给出这条卖出。若入口把 settings.overlays 换成
    OverlaysCfg()，脚本照样打印"今日无新信号"、照样写出只有表头的 CSV、照样 exit 0。
    """
    got = _run_daily_signal(tmp_path, monkeypatch, "true")
    assert list(got.columns) == run_daily_signal.CSV_COLUMNS
    assert got.to_dict("records") == [{
        "date": str(EXPECTED), "symbol": 600000, "strategy": "ma_cross",
        "action": "SELL", "close": 260.0}]


def test_run_daily_signal_says_nothing_when_the_config_turns_the_stop_off(tmp_path,
                                                                         monkeypatch):
    """同一段行情、开关关掉 → 零信号。

    没有这条对照，上一条测的可能只是"ma_cross 自己在末根卖出"；有了它，
    那条 SELL 的唯一来源只能是配置里打开的 atr_stop。
    """
    got = _run_daily_signal(tmp_path, monkeypatch, "false")
    assert got.empty, f"关掉止损后不该有任何信号，实际: {got.to_dict('records')}"


def test_the_stop_overlay_neither_invents_nor_erases_a_fresh_buy():
    """全市场扫描的"新 BUY"判据不受 ATR 止损影响，这是语义，不是巧合：
    overlay 只把 1 压成 0，而入场当根不可能被止损（peak = 当根收盘）——
    于是 0→1 既不会被抹掉，也不会被凭空造出来。

    钉住它的用处：M3 的趋势过滤**会**改变 BUY 判据（gate 挡住入场），
    见下一条——那条恒等在趋势过滤这一层**不成立**，而且不该成立。
    """
    for closes, base in ((TRIGGERED, ENTER_AT_2),
                         (BLOCKED, [0, 0, 1, 1, 1, 1, 1, 1, 1, 0, 1])):
        df, base = _padded(closes, base)
        on, _ = classify_and_scan(df, [Fixed(base)], EXPECTED, 5e7, overlays=stop())
        off, _ = classify_and_scan(df, [Fixed(base)], EXPECTED, 5e7, overlays=OFF)
        assert [s["strategy"] for s in on] == [s["strategy"] for s in off]


def test_the_trend_filter_does_erase_a_fresh_buy_in_a_downtrend():
    """全市场扫描的行为侧接线证明（补上设计 §2.1 末段欠的那条）。

    M2 时这个入口只能靠源码级实参断言：atr_stop 对"新 BUY"恒等（入场当根不可能
    被止损），所以任何行为测试都分不开"接了"与"没接"。趋势过滤打破了这条恒等——
    价格在长期均线下方时 gate 直接挡掉入场，于是"配置里开着过滤"这件事在
    classify_and_scan 的输出上**看得见**：同一段行情、同一个基础策略，
    开着过滤零信号，关掉过滤报一条 BUY。
    """
    df, base = _padded(GATE_BLOCK, GATE_BLOCK_BASE)
    strat = Fixed(base)
    on, skip_on = classify_and_scan(df, [strat], EXPECTED, 5e7, overlays=gate())
    off, skip_off = classify_and_scan(df, [strat], EXPECTED, 5e7, overlays=OFF)
    assert (skip_on, skip_off) == (None, None), "两趟都该完成判定，不该被跳过闸门拦下"
    assert off and [s["strategy"] for s in off] == ["fixed_stub"], \
        "关掉过滤必须报出这条 BUY，否则本测试没有分辨力"
    assert on == [], "价格在 MA(4) 下方，开着趋势过滤就不该报这次入场"
