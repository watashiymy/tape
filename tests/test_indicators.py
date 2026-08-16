import pandas as pd
import pytest

from quant.indicators import atr, ma, rolling_high, rolling_low
from tests.conftest import make_bars


def test_ma_hand_computed():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 11.0])   # 末段非对称：均值≠中位数
    out = ma(s, 3)
    assert pd.isna(out.iloc[1])                # 暖机期不得出值（min_periods 默认=n）
    assert out.iloc[2] == pytest.approx(2.0)   # (1+2+3)/3
    assert out.iloc[4] == pytest.approx(6.0)   # (3+4+11)/3


def test_rolling_high_low_include_current_bar():
    s = pd.Series([3.0, 1.0, 4.0, 1.0, 5.0, 0.5])
    # 含当日（spec §6）：若误写成 shift(1) 再滚动，下面两个值分别变成 4.0 / 1.0
    assert rolling_high(s, 3).iloc[4] == 5.0   # max(4,1,5)
    assert rolling_low(s, 3).iloc[5] == 0.5    # min(1,5,0.5)
    # 暖机期必须是 NaN：若 min_periods=1，唐奇安(Task 7)会拿"仅 1~2 根"的极值当通道，
    # 在样本开头凭空造出突破信号。
    assert pd.isna(rolling_high(s, 3).iloc[1])
    assert pd.isna(rolling_low(s, 3).iloc[1])


def test_atr_hand_computed():
    # b2 向下跳空（TR 由"与昨收的缺口"决定）、b3 不跳空且振幅最大（TR 由日内 high-low 决定），
    # 两种主导情形各覆盖一次；只覆盖其中一种，另一半算错也测不出来。
    df = make_bars([
        dict(date="2024-01-02", open=10, high=11, low=9, close=10, volume=1, amount=1),
        dict(date="2024-01-03", open=5, high=6, low=4, close=5, volume=1, amount=1),
        dict(date="2024-01-04", open=5, high=10, low=2, close=6, volume=1, amount=1),
    ])
    for c in ("open", "high", "low", "close"):
        df["adj_" + c] = df[c] * 2.0           # 后复权价 ≠ 原始价，钉死 atr 只读 adj_*
    out = atr(df, 2)
    # 后复权口径：TR2 = max(12-8, |12-20|, |8-20|) = 12（缺口项胜）
    #             TR3 = max(20-4, |20-10|, |4-10|) = 16（日内振幅胜）
    assert pd.isna(out.iloc[0])
    # 首根没有昨收，TR 按惯例退化为当日 high-low=4。这条同时钉住 max(axis=1) 的 skipna 语义：
    # 若改成 skipna=False 想让缺失更响亮，首根 TR 会变 NaN，整条 ATR 暖机静默推迟一天。
    assert out.iloc[1] == pytest.approx(8.0)   # (4+12)/2
    assert out.iloc[2] == pytest.approx(14.0)  # (12+16)/2
