import pandas as pd
import pytest

from quant.data.pipeline import prepare_bars
from tests.conftest import make_bars


def _row(d, px, **kw):
    base = dict(date=d, open=px, high=px * 1.01, low=px * 0.99, close=px,
                volume=1000, amount=px * 1000)
    base.update(kw)
    return base


def test_derives_adj_columns():
    raw = make_bars([_row("2024-01-02", 10.0, adj_factor=2.0)])
    df, _ = prepare_bars(raw)
    assert df.loc["2024-01-02", "adj_close"] == 20.0
    assert df.loc["2024-01-02", "adj_open"] == 20.0


def test_each_adj_column_scales_its_own_source():
    # 防 adj_high/adj_low 被错写成 close*factor：ATR(Task 5) 直接吃这两列，
    # 错算不报错、只会给出错的止损位。原用例 open==close 且不校验 high/low，抓不到。
    raw = make_bars([dict(date="2024-01-02", open=10.0, high=12.0, low=9.0, close=11.0,
                          volume=1000, amount=11000, adj_factor=2.0)])
    df, _ = prepare_bars(raw)
    r = df.loc["2024-01-02"]
    assert (r["adj_open"], r["adj_high"], r["adj_low"], r["adj_close"]) == (20.0, 24.0, 18.0, 22.0)


def test_filters_suspended_rows():
    raw = make_bars([
        _row("2024-01-02", 10.0),
        _row("2024-01-03", 10.0, trade_status=0, volume=0),  # 停牌行
        _row("2024-01-04", 11.0),
    ])
    df, _ = prepare_bars(raw)
    assert len(df) == 2  # 停牌行被过滤（spec 决策6）
    assert "2024-01-03" not in df.index.strftime("%Y-%m-%d")


def test_dedup_and_warn():
    raw = make_bars([_row("2024-01-02", 10.0), _row("2024-01-02", 10.5)])
    df, warns = prepare_bars(raw)
    assert len(df) == 1
    assert any("重复" in w for w in warns)


def test_jump_warning_only_when_factor_unchanged():
    # 单日 -20%，因子未变 → 告警；因子变化（除权日）→ 不告警
    raw1 = make_bars([_row("2024-01-02", 10.0), _row("2024-01-03", 8.0)])
    _, warns1 = prepare_bars(raw1)
    assert any("跳变" in w for w in warns1)

    raw2 = make_bars([
        _row("2024-01-02", 10.0, adj_factor=1.0),
        _row("2024-01-03", 8.0, adj_factor=1.25),  # 除权日
    ])
    _, warns2 = prepare_bars(raw2)
    assert not any("跳变" in w for w in warns2)


def test_ohlc_sanity():
    bad = make_bars([_row("2024-01-02", 10.0, high=9.0, low=11.0)])  # high < low
    df, warns = prepare_bars(bad)
    assert len(df) == 0
    assert any("OHLC" in w for w in warns)


def test_ohlc_sanity_catches_high_below_close():
    # high > low 成立，但 high < close：只有完整谓词拦得住
    bad = make_bars([dict(date="2024-01-02", open=10.0, high=10.5, low=9.0, close=11.0,
                          volume=1000, amount=11000)])
    df, warns = prepare_bars(bad)
    assert len(df) == 0
    assert any("OHLC" in w for w in warns)


def test_ohlc_sanity_catches_low_above_open():
    bad = make_bars([dict(date="2024-01-02", open=9.5, high=11.0, low=9.8, close=10.0,
                          volume=1000, amount=10000)])
    df, warns = prepare_bars(bad)
    assert len(df) == 0
    assert any("OHLC" in w for w in warns)


@pytest.mark.parametrize("col", ["open", "high", "low", "close", "amount", "adj_factor"])
def test_nan_in_price_column_is_dropped_and_warns(col):
    # baostock 的空串经 pd.to_numeric(errors="coerce") 会变 NaN。NaN 与任何数比较恒为 False，
    # 挡不住 OHLC 谓词；派生出的 adj_* 同样是 NaN，指标层不报错、只给一串 NaN。
    # make_bars 拒收 NaN，所以在构造之后注入（模拟线上数据洞）。
    raw = make_bars([_row("2024-01-02", 10.0), _row("2024-01-03", 10.0), _row("2024-01-04", 10.0)])
    raw.loc[pd.Timestamp("2024-01-03"), col] = float("nan")
    df, warns = prepare_bars(raw)
    assert list(df.index.strftime("%Y-%m-%d")) == ["2024-01-02", "2024-01-04"]
    assert not df[["adj_open", "adj_high", "adj_low", "adj_close"]].isna().any().any()
    assert any("缺失" in w for w in warns)


def test_nan_close_does_not_swallow_jump_warning():
    # NaN 会让自身与次日两天的 pct_change 都变 NaN，把真实跳变告警一并吞掉。
    raw = make_bars([_row("2024-01-02", 10.0), _row("2024-01-03", 10.0),
                     _row("2024-01-04", 20.0), _row("2024-01-05", 20.0)])
    raw.loc[pd.Timestamp("2024-01-03"), "close"] = float("nan")
    _, warns = prepare_bars(raw)
    assert any("跳变" in w for w in warns)  # 剔除 NaN 行后 10 → 20 的跳变必须仍被发现


def test_suspended_rows_removed_before_adj_and_warnings():
    # 钉住清洗顺序：停牌行必须先剔除，再派生 adj_* / 出告警。
    # 停牌行故意带 is_st=1 与异常 adj_factor：顺序一旦调换，ST 告警会被它触发。
    raw = make_bars([
        _row("2024-01-02", 10.0),
        _row("2024-01-03", 10.0, trade_status=0, volume=0, is_st=1, adj_factor=99.0),
        _row("2024-01-04", 10.0),
    ])
    df, warns = prepare_bars(raw)
    assert list(df.index.strftime("%Y-%m-%d")) == ["2024-01-02", "2024-01-04"]
    assert (df["adj_factor"] == 1.0).all()
    assert not any("ST" in w for w in warns)


def test_st_period_warns_but_keeps_rows():
    # spec 决策7：股票池应剔除 ST，但历史区间内曾被 ST 的标的要告警提醒（数据仍保留）
    raw = make_bars([
        _row("2024-01-02", 10.0),
        _row("2024-01-03", 10.0, is_st=1),
        _row("2024-01-04", 10.0, is_st=1),
    ])
    df, warns = prepare_bars(raw)
    assert len(df) == 3
    assert any("ST" in w for w in warns)
