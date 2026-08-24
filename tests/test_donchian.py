import pytest

from quant.strategy.donchian import Donchian
from tests.conftest import make_bars


def _bars(closes, amounts, factors=None):
    """closes 传的是**后复权价**路径，raw = adj / factor。
    factor 必须能出现台阶（除权日），否则 raw 与 adj_* 恒等，
    "突破判定误用原始价"这一错法就测不出来（spec 决策1，与 test_ma_cross 同一套路）。"""
    factors = factors or [1.0] * len(closes)
    rows = [dict(date=f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                 open=c / f, high=c / f, low=c / f, close=c / f,
                 volume=1000, amount=a, adj_factor=f)
            for i, (c, a, f) in enumerate(zip(closes, amounts, factors))]
    df = make_bars(rows)
    for col in ("open", "high", "low", "close"):
        df["adj_" + col] = df[col] * df["adj_factor"]
    return df


def test_dead_signal_regression():
    """spec §13：持续创新高 + 持续放量的序列必须触发信号。
    若滚动窗口误含当日（未 shift），"收盘 > N日最高"永不成立 → 本测试失败。"""
    n = 40
    closes = [10.0 + i * 0.5 for i in range(n)]            # 每天创新高
    amounts = [1e6 * (1.6 ** min(i, 12)) for i in range(n)]  # 每天成交额 > 1.5×前均额
    pos = Donchian(entry_n=20, exit_n=10, amount_n=20, amount_ratio=1.5).generate_positions(
        _bars(closes, amounts))
    assert pos.sum() > 0, "死信号：突破窗口可能误含当日（须先 shift(1) 再滚动）"
    assert pos.iloc[-1] == 1


def test_exit_on_breakdown():
    up = [10.0 + i * 0.5 for i in range(30)]
    down = [25.0 - i * 2.0 for i in range(1, 8)]            # 快速击穿 10 日低点
    closes = up + down
    amounts = [1e6 * (1.6 ** min(i, 12)) for i in range(len(closes))]
    pos = Donchian(entry_n=20, exit_n=10, amount_n=20, amount_ratio=1.5).generate_positions(
        _bars(closes, amounts))
    # 逐日锁死整条路径，而不是只查两个端点：
    # 入场在 idx20（第一个"创新高 + 成交额 2.07 倍于前 20 日均额"的日子；idx24 起量比
    # 跌回 1.47 < 1.5，所以只有 idx20 这一次入场机会）；
    # 出场在 idx32（close=19.0 < 前 10 日最低 21.0，idx30/31 的 23.0/21.0 都还没跌破）。
    assert list(pos) == [0] * 20 + [1] * 12 + [0] * 5
    assert pos.iloc[29] == 1     # 上升末端仍持有
    assert pos.iloc[-1] == 0     # 跌破前 10 日最低 → 空仓


def test_no_entry_without_amount_expansion():
    closes = [10.0 + i * 0.5 for i in range(40)]
    amounts = [1e6] * 40                                    # 成交额平稳 → 量能条件不满足
    pos = Donchian(entry_n=20, exit_n=10, amount_n=20, amount_ratio=1.5).generate_positions(
        _bars(closes, amounts))
    assert pos.sum() == 0


def test_signal_uses_adjusted_price():
    """spec 决策1：突破判定必须用后复权价 adj_close，用原始价会被除权缺口骗出假信号。

    后复权价一路上涨、期间遇 10 送 10 除权日：正确实现全程持有；
    误用原始价则在除权日因 raw 腰斩被判"跌破前 10 日最低"而假出场，
    此后量能条件不再满足、再也进不来 —— 凭空造出一次往返交易。
    """
    n = 45
    closes = [10.0 + i * 0.5 for i in range(n)]     # 后复权价每天创新高
    factors = [1.0] * 30 + [2.0] * 15               # idx30 为 10 送 10 除权日，raw 价腰斩
    amounts = [1e6] * 20 + [2.5e6] + [1e6] * 24     # 只有 idx20 这一天放量，仅一次入场机会
    pos = Donchian(entry_n=20, exit_n=10, amount_n=20, amount_ratio=1.5).generate_positions(
        _bars(closes, amounts, factors))
    assert list(pos) == [0] * 20 + [1] * 25


def test_amount_ratio_threshold_is_enforced():
    """放量但未达 amount_ratio 倍数时不得入场。

    test_no_entry_without_amount_expansion 用的是"完全平稳"的成交额（amt 恰好等于均额），
    靠严格 > 就挡住了 —— 把 amount_ratio 改成 1.0、甚至把倍数整个删掉，那条测试照样通过。
    要锁住阈值倍数，必须用"确实在放量、但只有 1.3 倍"的序列。
    """
    n = 45
    closes = [10.0 + i * 0.5 for i in range(n)]      # 每天创新高，价格条件恒满足
    amounts = [1e6] * 20
    for i in range(20, n):
        amounts.append(1.3 * sum(amounts[i - 20:i]) / 20)   # 恒为前 20 日均额的 1.3 倍
    pos = Donchian(entry_n=20, exit_n=10, amount_n=20, amount_ratio=1.5).generate_positions(
        _bars(closes, amounts))
    assert pos.sum() == 0, "成交额只有前均额的 1.3 倍，未达 1.5 倍阈值，不应入场"


def test_amount_baseline_excludes_today():
    """成交额基准是"前 amount_n 日均额"，当日那根放量柱不能算进分母。

    amount_n=5、当日 1.6e6 vs 前 5 日均额 1.0e6 → 1.6 倍 > 1.5 倍，应当入场；
    若基准误含当日，分母变成 (4×1.0+1.6)/5 = 1.12e6，阈值抬到 1.68e6 → 反而进不去。
    """
    closes = [10.0 + i * 0.5 for i in range(30)]
    amounts = [1e6] * 20 + [1.6e6] + [1e6] * 9
    pos = Donchian(entry_n=20, exit_n=10, amount_n=5, amount_ratio=1.5).generate_positions(
        _bars(closes, amounts))
    assert list(pos) == [0] * 20 + [1] * 10


def test_window_params_are_wired_distinctly():
    """entry_n / exit_n / amount_n 三个窗口各接各的。

    默认参数下 entry_n == amount_n == 20，接线接错也测不出来；这里三个值互不相同：
    - 入场 idx15：20.0 突破前 15 日最高 17.0，且 2e6 > 1.5 × 前 3 日均额 1e6
      （若 amount_n 误接 entry_n=15，基准变成前 15 日均额 2.6e6，阈值 3.9e6 → 全程空仓）
    - 出场 idx23：22.0 < 前 5 日最低 23.0
      （若出场窗口误接 entry_n=15，前 15 日最低是 14.0 → 永不出场，一路持有到底）
    """
    closes = ([10.0 + i * 0.5 for i in range(15)]           # idx0~14 暖机，10.0 → 17.0
              + [20.0, 21.0, 22.0, 23.0, 24.0, 25.0]        # idx15 突破入场，随后续涨
              + [24.0, 23.0, 22.0, 21.0, 20.0]              # idx21~25 回落
              + [20.0] * 4)                                 # idx26~29 走平
    amounts = [3e6] * 12 + [1e6] * 3 + [2e6] + [1e6] * 14   # 前期活跃 → 缩量整理 → 放量突破
    pos = Donchian(entry_n=15, exit_n=5, amount_n=3, amount_ratio=1.5).generate_positions(
        _bars(closes, amounts))
    assert list(pos) == [0] * 15 + [1] * 8 + [0] * 7


def test_entry_requires_strictly_greater():
    """入场的两个判据都是严格大于：等于阈值不算突破，也不算放量。"""
    closes = [10.0 + i * 0.5 for i in range(20)] + [19.5, 20.0] + [20.0] * 8
    amounts = [1e6] * 20 + [2e6, 1.575e6] + [1e6] * 8
    pos = Donchian(entry_n=20, exit_n=10, amount_n=20, amount_ratio=1.5).generate_positions(
        _bars(closes, amounts))
    # idx20：close 19.5 恰等于前 20 日最高 19.5（量能充足），> 而非 >= 才不入场
    assert pos.iloc[20] == 0, "收盘等于前 N 日最高不算突破，价格判据须为 >"
    # idx21：close 20.0 确实突破，但成交额 1.575e6 恰等于 1.5 × 前 20 日均额 1.05e6
    assert pos.iloc[21] == 0, "成交额恰等于阈值不算放量，量能判据须为 >"
    assert pos.sum() == 0


def test_exit_requires_strictly_less():
    """出场判据是严格小于：收盘恰好等于前 exit_n 日最低不算跌破，继续持有。"""
    closes = [10.0 + i * 0.5 for i in range(20)] + [20.0] * 15   # 突破后走平在 20.0
    amounts = [1e6] * 20 + [2e6] + [1e6] * 14
    pos = Donchian(entry_n=20, exit_n=10, amount_n=20, amount_ratio=1.5).generate_positions(
        _bars(closes, amounts))
    # idx30 起前 10 日最低恒为 20.0，与当日收盘相等；判据若写成 <= 会在这里假出场
    assert list(pos) == [0] * 20 + [1] * 15


def test_no_lookahead():
    closes = [10.0 + i * 0.5 for i in range(40)]
    amounts = [1e6 * (1.6 ** min(i, 12)) for i in range(40)]
    strat = Donchian(entry_n=20, exit_n=10, amount_n=20, amount_ratio=1.5)
    full = strat.generate_positions(_bars(closes, amounts))
    trunc = strat.generate_positions(_bars(closes[:-5], amounts[:-5]))
    assert (full.iloc[: len(trunc)].values == trunc.values).all()


@pytest.mark.parametrize("kw", [
    dict(entry_n=0), dict(exit_n=0), dict(amount_n=0),      # rolling(0) 静默全 NaN
    dict(entry_n=-5), dict(exit_n=-1), dict(amount_n=-3),
    dict(entry_n=20.0), dict(exit_n=10.0), dict(amount_n=20.0),  # 浮点窗口
    dict(amount_ratio=0), dict(amount_ratio=-1.5), dict(amount_ratio="1.5"),
])
def test_invalid_params_raise_at_construction(kw):
    """回归：pandas 的 rolling(0) 静默返回全 NaN（无警告）——
    exit_n=0 时入场后价格腰斩仍满仓永不卖出；entry_n=0/amount_n=0 全程静默空仓，
    回测照常完成零告警，产出退化策略的错误结论。浮点窗口迟至 generate_positions
    才崩且报错误导。必须在构造期就 raise ValueError（不用 assert：-O 下会被剥除）。"""
    with pytest.raises(ValueError):
        Donchian(**kw)


def test_invalid_param_error_names_param_and_value():
    """报错必须带参数名和实际值，用户才能对着 settings.yaml 定位改哪一行。"""
    with pytest.raises(ValueError, match=r"exit_n.*0"):
        Donchian(exit_n=0)
    with pytest.raises(ValueError, match=r"amount_ratio.*-1\.5"):
        Donchian(amount_ratio=-1.5)


def test_valid_params_still_construct():
    """反向断言：校验不能误伤合法参数（含 amount_ratio 给整数）。"""
    s = Donchian(entry_n=20, exit_n=10, amount_n=20, amount_ratio=1.5)
    assert (s.entry_n, s.exit_n, s.amount_n, s.amount_ratio) == (20, 10, 20, 1.5)
    assert Donchian(entry_n=1, exit_n=1, amount_n=1, amount_ratio=2).amount_ratio == 2
