import pandas as pd


def make_bars(rows: list[dict]) -> pd.DataFrame:
    """手工构造日线 DataFrame。rows 每项至少含 date/open/high/low/close/volume/amount，
    可选 adj_factor（默认1.0）/trade_status（默认1）/is_st（默认0）。"""
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    for col, default in [("adj_factor", 1.0), ("trade_status", 1), ("is_st", 0)]:
        if col not in df.columns:
            df[col] = default
        else:
            # 关键：只有部分行显式给了该列时，pandas 会把其余行填成 NaN。
            # 少了这一步，"只给一行 trade_status=0"的用例会让全部行都不等于 1 而被过滤光。
            df[col] = df[col].fillna(default)
    return df
