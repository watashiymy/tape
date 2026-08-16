from quant.strategy.donchian import Donchian
from tests.conftest import make_bars


def _bars(closes, amounts):
    rows = [dict(date=f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                 open=c, high=c, low=c, close=c, volume=1000, amount=a)
            for i, (c, a) in enumerate(zip(closes, amounts))]
    df = make_bars(rows)
    for col in ("open", "high", "low", "close"):
        df["adj_" + col] = df[col]
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
    assert pos.iloc[29] == 1     # 上升末端仍持有
    assert pos.iloc[-1] == 0     # 跌破前 10 日最低 → 空仓


def test_no_entry_without_amount_expansion():
    closes = [10.0 + i * 0.5 for i in range(40)]
    amounts = [1e6] * 40                                    # 成交额平稳 → 量能条件不满足
    pos = Donchian(entry_n=20, exit_n=10, amount_n=20, amount_ratio=1.5).generate_positions(
        _bars(closes, amounts))
    assert pos.sum() == 0


def test_no_lookahead():
    closes = [10.0 + i * 0.5 for i in range(40)]
    amounts = [1e6 * (1.6 ** min(i, 12)) for i in range(40)]
    strat = Donchian(entry_n=20, exit_n=10, amount_n=20, amount_ratio=1.5)
    full = strat.generate_positions(_bars(closes, amounts))
    trunc = strat.generate_positions(_bars(closes[:-5], amounts[:-5]))
    assert (full.iloc[: len(trunc)].values == trunc.values).all()
