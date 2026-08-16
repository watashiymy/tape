import pandas as pd
import pytest

from quant.indicators import atr, ma, rolling_high, rolling_low
from tests.conftest import make_bars


def test_ma_hand_computed():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = ma(s, 3)
    assert pd.isna(out.iloc[1])
    assert out.iloc[2] == pytest.approx(2.0)   # (1+2+3)/3
    assert out.iloc[4] == pytest.approx(4.0)   # (3+4+5)/3


def test_rolling_high_low_include_current_bar():
    s = pd.Series([3.0, 1.0, 4.0, 1.0, 5.0])
    assert rolling_high(s, 3).iloc[4] == 5.0   # max(4,1,5)，含当日（spec §6 窗口约定）
    assert rolling_low(s, 3).iloc[4] == 1.0


def test_atr_hand_computed():
    df = make_bars([
        dict(date="2024-01-02", open=10, high=11, low=9, close=10, volume=1, amount=1),
        dict(date="2024-01-03", open=10, high=12, low=10, close=11, volume=1, amount=1),
        dict(date="2024-01-04", open=11, high=11, low=8, close=9, volume=1, amount=1),
    ])
    for c in ("open", "high", "low", "close"):
        df["adj_" + c] = df[c]
    out = atr(df, 2)
    # TR2 = max(12-10, |12-10|, |10-10|) = 2；TR3 = max(3, |11-11|, |8-11|) = 3
    assert out.iloc[2] == pytest.approx(2.5)   # (2+3)/2
