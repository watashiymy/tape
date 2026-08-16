import pandas as pd

REQUIRED = ["open", "high", "low", "close", "volume", "amount"]


def make_bars(rows: list[dict]) -> pd.DataFrame:
    """手工构造日线 DataFrame。rows 每项须含 date + REQUIRED 全部列，
    可选 adj_factor（默认1.0）/trade_status（默认1）/is_st（默认0）。"""
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    for col, default in [("adj_factor", 1.0), ("trade_status", 1), ("is_st", 0)]:
        if col not in df.columns:
            df[col] = default
        else:
            # 关键：只有部分行显式给了该列时，pandas 会把其余行填成 NaN。
            # 少了 fillna，"只给一行 trade_status=0"的用例会让全部行都不等于 1 而被过滤光；
            # 少了 astype，dtype 会变成 float，与线上永远是 int 的形态不符
            # （断言 [1, 0] 察觉不到，因为 1.0 == 1）。
            df[col] = df[col].fillna(default).astype(type(default))
    # 必填列漏写只会得到一列 NaN，而 NaN 参与比较恒为 False：
    # 例如 Task 7 的成交额过滤会静默不出信号，测试还"通过"——测的却是错的东西。
    missing = [c for c in REQUIRED if c not in df.columns or df[c].isna().any()]
    if missing:
        raise ValueError(f"make_bars: 必填列缺失或含 NaN: {missing}")
    return df
