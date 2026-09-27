from datetime import date

import numpy as np
import pandas as pd
import pytest

from quant.market import breadth

D = lambda s: date.fromisoformat(s)
DAYS = [D(f"2026-09-{d:02d}") for d in (1, 2, 3, 4, 7, 8)]      # 6 个交易日


def snap(rows):
    """rows: [(code, close, preclose, tradestatus, isST)]"""
    return pd.DataFrame(rows, columns=["code", "close", "preclose", "tradestatus", "isST"])


def test_adjusted_chain_handles_ex_rights_and_suspension():
    snaps = {
        DAYS[0]: snap([("a", 10.0, 10.0, 1, 0)]),
        DAYS[1]: snap([("a", 11.0, 10.0, 1, 0)]),
        DAYS[2]: snap([("a", 5.5, 5.5, 1, 0)]),          # 除权：昨收折算 11→5.5，价格没跌
        DAYS[3]: snap([("a", 5.5, 5.5, 0, 0)]),          # 停牌：比值 1，不计根数
        DAYS[4]: snap([("a", 6.05, 5.5, 1, 0)]),
        DAYS[5]: snap([("a", 6.05, 6.05, 1, 0)]),
    }
    adj = breadth.adjusted_close(snaps, DAYS)["a"]
    assert adj.iloc[0] == pytest.approx(1.0)
    assert adj.iloc[1] == pytest.approx(1.1) and adj.iloc[2] == pytest.approx(1.1)   # 除权日不跌
    assert adj.iloc[3] == pytest.approx(1.1) and adj.iloc[4] == pytest.approx(1.21)
    bars = breadth.bar_index(snaps, DAYS)["a"]
    assert list(bars) == [1, 2, 3, 3, 4, 5], "停牌日不计入根数"


def test_ex_rights_on_a_suspended_day_is_not_a_drop():
    snaps = {
        DAYS[0]: snap([("a", 10.0, 10.0, 1, 0)]),
        DAYS[1]: snap([("a", 5.0, 5.0, 0, 0)]),          # 停牌当天除权：baostock 给折算后的 preclose 与 close
        DAYS[2]: snap([("a", 5.0, 5.0, 1, 0)]),
    }
    adj = breadth.adjusted_close(snaps, DAYS[:3])["a"]
    assert adj.iloc[2] == pytest.approx(1.0), "复牌日不能把除权缺口当成真跌"


def test_ma_breadth_counts_only_stocks_with_enough_bars():
    # 两只票，N=3：a 涨、b 跌；c 只有两根，不进分母
    closes = {"a": [1, 2, 3, 4, 5, 6], "b": [6, 5, 4, 3, 2, 1], "c": [None, None, None, None, 1, 1]}
    snaps = {}
    for i, d in enumerate(DAYS):
        rows = []
        for code, series in closes.items():
            if series[i] is None:
                continue
            prev = series[i - 1] if i and series[i - 1] is not None else series[i]
            rows.append((code, float(series[i]), float(prev), 1, 0))
        snaps[d] = snap(rows)
    table = breadth.compute(snaps, DAYS, windows=(3,), high_low_window=3)
    last = table.iloc[-1]
    assert (last["above3"], last["n3"]) == (1, 2), "a 在均线上、b 在下、c 历史不足"
    assert (last["new_high"], last["new_low"], last["n_hl"]) == (1, 1, 2)


def test_gap_day_poisons_the_lookback_window():
    snaps = {d: snap([("a", 1.0 + i * 0.1, 1.0 + (i - 1) * 0.1 if i else 1.0, 1, 0)])
             for i, d in enumerate(DAYS) if d != DAYS[2]}          # 9-03 整天缺
    table = breadth.compute(snaps, DAYS, windows=(3,), high_low_window=3)
    by = table.set_index("date")
    assert by.loc[DAYS[3], "n3"] == 0 and by.loc[DAYS[4], "n3"] == 0   # 回看跨过缺口 → 不进分母
    assert by.loc[DAYS[5], "n3"] == 1                                   # 3 根都在缺口之后了


def test_single_stock_missing_row_is_a_gap_for_that_stock_only():
    snaps = {}
    for i, d in enumerate(DAYS):
        rows = [("b", 2.0, 2.0, 1, 0)]
        if d != DAYS[2]:
            rows.append(("a", 1.0, 1.0, 1, 0))
        snaps[d] = snap(rows)
    table = breadth.compute(snaps, DAYS, windows=(2,), high_low_window=2).set_index("date")
    assert table.loc[DAYS[3], "n2"] == 1 and table.loc[DAYS[5], "n2"] == 2


def test_st_and_suspended_are_excluded_from_the_denominator():
    snaps = {d: snap([("a", 1.0, 1.0, 1, 0), ("s", 1.0, 1.0, 1, 1), ("h", 1.0, 1.0, 0, 0)])
             for d in DAYS}
    table = breadth.compute(snaps, DAYS, windows=(2,), high_low_window=2)
    assert table.iloc[-1]["n2"] == 1


def test_tie_with_the_rolling_high_counts_as_new_high_within_tolerance():
    vals = [1.0, 1.2, 1.1, 1.2]
    snaps = {d: snap([("a", v, vals[i - 1] if i else v, 1, 0)]) for i, (d, v) in enumerate(zip(DAYS, vals))}
    table = breadth.compute(snaps, DAYS[:4], windows=(2,), high_low_window=3)
    assert table.iloc[-1]["new_high"] == 1


def test_output_columns():
    snaps = {d: snap([("a", 1.0, 1.0, 1, 0)]) for d in DAYS}
    cols = list(breadth.compute(snaps, DAYS).columns)
    assert cols == ["date", "above20", "n20", "above60", "n60", "above200", "n200",
                    "new_high", "new_low", "n_hl"]


def test_suspicious_suspended_lists_only_odd_rows():
    snaps = {DAYS[0]: snap([("a", 1.0, 1.0, 0, 0), ("b", 1.1, 1.0, 0, 0), ("c", 2.0, 1.0, 1, 0)])}
    out = breadth.suspicious_suspended(snaps)
    assert len(out) == 1 and "b" in out[0]


def test_vectorized_rolling_matches_reference_on_random_gappy_data():
    """向量化 `_rolling_on_bars` 必须和逐列参考实现在随机停牌/缺口/ST 数据上逐格相等。"""
    rng = np.random.default_rng(20260927)
    n_codes, n_days = 40, 60
    days = [D("2026-01-01") + (date(2026, 1, 2) - date(2026, 1, 1)) * i for i in range(n_days)]
    codes = [f"c{k:02d}" for k in range(n_codes)]
    rows_per_day = []
    for i in range(n_days):
        rows = []
        for code in codes:
            if rng.random() < 0.03:                        # 随机整天缺一只票（毒 bar）
                continue
            tradestatus = 0 if rng.random() < 0.1 else 1    # 随机停牌
            isst = 1 if rng.random() < 0.1 else 0
            prev_close = float(rng.uniform(5, 15))
            close = prev_close if tradestatus == 0 else float(rng.uniform(5, 15))
            rows.append((code, close, prev_close, tradestatus, isst))
        rows_per_day.append(rows)
    snaps = {d: snap(rows) for d, rows in zip(days, rows_per_day)}

    adj = breadth.adjusted_close(snaps, days)
    trading = (breadth._wide(snaps, days, "tradestatus").fillna(0).astype(int) == 1)
    for n, fn in ((3, "mean"), (5, "mean"), (7, "max"), (7, "min")):
        got = breadth._rolling_on_bars(adj, trading, n, fn)
        want = breadth._rolling_on_bars_reference(adj, trading, n, fn)
        pd.testing.assert_frame_equal(got, want, check_exact=False, rtol=1e-9, atol=1e-12)
