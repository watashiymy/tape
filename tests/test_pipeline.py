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
