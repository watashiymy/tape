import shutil
from pathlib import Path

import pandas as pd

REQUIRED = ["open", "high", "low", "close", "volume", "amount"]

APP_DIR = Path(__file__).resolve().parent.parent / "app"


def copy_app(tmp_path: Path) -> Path:
    """把整个 app/ 目录复制到 tmp_path/app，返回复制后的 dashboard.py 路径。

    复制而不是直接跑仓库里那份：dashboard.py 的 ROOT 是从 __file__ 推出来的，
    复制后 OUTPUT / RUNS_DIR 全落在 tmp_path，与仓库真实产物完全隔离
    （否则测试会读到、甚至停掉真任务）。
    必须**整目录**复制：dashboard.py 自 v0.2.1 起 `import theme`（同目录的
    app/theme.py），只复制 dashboard.py 一个文件会 ModuleNotFoundError。
    """
    app_dir = tmp_path / "app"
    shutil.copytree(APP_DIR, app_dir, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    return app_dir / "dashboard.py"


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
