"""baostock 数据源实现（spec §5）。用法：with BaostockProvider() as p: ..."""
from __future__ import annotations

from datetime import date

import baostock as bs
import pandas as pd

from quant.data.provider import DataProvider

_K_FIELDS = "date,open,high,low,close,volume,amount,tradestatus,isST"
_NUM_COLS = ["open", "high", "low", "close", "volume", "amount"]
_EMPTY_COLS = _NUM_COLS + ["adj_factor", "trade_status", "is_st"]
_MAINBOARD_PREFIXES = ("sh.60", "sz.00")  # 沪主板 600/601/603/605；深主板 000/001 + 原中小板 002/003
_MIN_LISTED_NATURAL_DAYS = 400            # 新股暖机门槛（自然日，≈270 根 K 线 > MA60 两倍）


def to_bs_code(symbol: str) -> str:
    """'600519' → 'sh.600519'；'000333' → 'sz.000333'。6 开头沪市，其余(0/3开头)深市。"""
    return ("sh." if symbol.startswith("6") else "sz.") + symbol


class BaostockError(RuntimeError):
    """baostock 返回非 0 错误码。code 是字符串（baostock 的口径）。"""
    SESSION_CODES = frozenset({"10001001", "10001003", "10001007", "10002001", "10002002",
                               "10002003", "10002004", "10002005", "10002006", "10002007", "10002008"})

    def __init__(self, code: str, msg: str):
        super().__init__(f"baostock 错误 {code}: {msg}")
        self.code = code

    @property
    def is_session_error(self) -> bool:
        return self.code in self.SESSION_CODES


class SnapshotInvalid(ValueError):
    """全市场快照没通过校验——不存、报错说明是哪一天（spec §5.2）。"""


def _check(rs) -> None:
    if rs.error_code != "0":
        raise BaostockError(rs.error_code, rs.error_msg)


SNAPSHOT_MIN_ROWS = 4000
SNAPSHOT_COLUMNS = ["code", "close", "preclose", "tradestatus", "isST"]


def _fetch(rs) -> pd.DataFrame:
    """把 baostock 结果集读成 DataFrame。

    **必须用这个函数，不要用 rs.get_data()。** baostock 0.9.3 的 get_data() 在翻页分支里
    调用了 pandas 2.0 已删除的 DataFrame.append，任何首页恰好返回 2000 行（分页大小）的
    查询都会抛 AttributeError——10 年日线（约 2579 行）正好命中，实测必崩。
    官方惯用的 next()+get_row_data() 逐行迭代没有这个问题，已用真实数据验证通过。
    """
    _check(rs)
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    # 翻页请求失败时 next() 只是把服务端错误码写进 rs.error_code 后 return False，不抛异常，
    # 与"读完了"无法区分。少查这一次，残缺的半截历史会被当成完整数据喂给回测。
    _check(rs)
    return pd.DataFrame(rows, columns=rs.fields)


def _factors_to_series(fac: pd.DataFrame) -> pd.Series:
    """复权因子表 → 日期索引的 float Series（供 reindex(ffill) 展开到日频）。

    空表（新股无除权记录）必须返回**空 DatetimeIndex** 的 Series：默认 RangeIndex 的
    空 Series 在 reindex(DatetimeIndex, method='ffill') 时直接
    TypeError: Cannot compare dtypes int64 and datetime64[us]，兜底等于没兜。
    """
    if fac.empty:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    idx = pd.to_datetime(fac["dividOperateDate"])
    return pd.Series(fac["backAdjustFactor"].astype(float).values, index=idx).sort_index()


def _filter_scan_universe(all_df: pd.DataFrame, basic_df: pd.DataFrame,
                          as_of: date) -> pd.DataFrame:
    """扫描池过滤（纯函数，离线可测；设计文档 §3.1）。

    all_df: query_all_stock 结果（code/tradeStatus/code_name，当日在市清单）
    basic_df: query_stock_basic 结果（code/code_name/ipoDate/outDate/type/status）
    二者取交集（inner merge），再过滤：主板前缀；名称含 'ST'；type=1 且 status=1；
    ipoDate 距 as_of 不足 _MIN_LISTED_NATURAL_DAYS 自然日。
    名称取 all_df 的 code_name（扫描日的现用名，ST 帽子以它为准）。
    返回 DataFrame[symbol(6位str), name]，按 symbol 升序、RangeIndex。
    """
    df = all_df[["code", "code_name"]].merge(
        basic_df[["code", "ipoDate", "type", "status"]], on="code", how="inner")
    mainboard = df["code"].str.startswith(_MAINBOARD_PREFIXES)
    not_st = ~df["code_name"].str.contains("ST")
    listed_stock = (df["type"] == "1") & (df["status"] == "1")
    # errors="coerce"：脏 ipoDate 变 NaT，比较恒 False → 该票被剔除而非静默放行
    ipo = pd.to_datetime(df["ipoDate"], errors="coerce")
    seasoned = ipo <= pd.Timestamp(as_of) - pd.Timedelta(days=_MIN_LISTED_NATURAL_DAYS)
    df = df[mainboard & not_st & listed_stock & seasoned]
    out = pd.DataFrame({"symbol": df["code"].str[3:], "name": df["code_name"]})
    return out.sort_values("symbol").reset_index(drop=True)


class BaostockProvider(DataProvider):
    def __enter__(self):
        _check(bs.login())
        return self

    def __exit__(self, *exc):
        bs.logout()
        return False

    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        code = to_bs_code(symbol)
        rs = bs.query_history_k_data_plus(
            code, _K_FIELDS, start_date=str(start), end_date=str(end),
            frequency="d", adjustflag="3")  # 3 = 不复权（原始价）
        df = _fetch(rs)
        if df.empty:
            return pd.DataFrame(columns=_EMPTY_COLS,
                                index=pd.DatetimeIndex([], name="date"))
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        for c in _NUM_COLS:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["trade_status"] = pd.to_numeric(df.pop("tradestatus"), errors="coerce").fillna(0).astype(int)
        df["is_st"] = pd.to_numeric(df.pop("isST"), errors="coerce").fillna(0).astype(int)
        df["adj_factor"] = self._adj_factor_series(code, end).reindex(df.index, method="ffill").fillna(1.0)
        return df[_EMPTY_COLS]

    @staticmethod
    def _adj_factor_series(code: str, end: date) -> pd.Series:
        # 从上市早期拉全量因子记录（只在除权除息日有记录），ffill 到日频。
        # 探针已确认 backAdjustFactor 是"自上市累积"口径（单调不减、每个除权日一条、日期无重复），
        # 所以直接 ffill 即可，无需累乘。
        rs = bs.query_adjust_factor(code=code, start_date="1990-01-01", end_date=str(end))
        return _factors_to_series(_fetch(rs))

    def get_index_daily(self, index_code: str, start: date, end: date) -> pd.DataFrame:
        code = "sh." + index_code if index_code.startswith("0") else index_code
        rs = bs.query_history_k_data_plus(
            code, "date,close", start_date=str(start), end_date=str(end),
            frequency="d", adjustflag="3")
        df = _fetch(rs)
        if df.empty:
            raise ValueError(f"指数 {index_code} 在 {start}~{end} 无数据（检查代码前缀是否为 sh./sz.）")
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        return df

    def get_all_symbols(self, as_of: date) -> pd.DataFrame:
        all_df = _fetch(bs.query_all_stock(day=str(as_of)))
        if all_df.empty:
            # 非交易日或当日数据未更新（约 17:30 后才有）时返回空表，必须炸而不是
            # 让扫描"成功"跑出 0 只标的。
            raise ValueError(f"query_all_stock({as_of}) 返回空：{as_of} 非交易日，"
                             "或当日清单尚未更新（约 17:30 后可用）")
        basic_df = _fetch(bs.query_stock_basic())
        return _filter_scan_universe(all_df, basic_df, as_of)

    def get_trade_calendar(self, start: date, end: date) -> list[date]:
        # 注意：query_trade_dates 返回的是**日历日**（含周末节假日），需按 is_trading_day 过滤。
        # 10 年区间约 3879 个日历日 → 2579 个交易日（实测）。
        rs = bs.query_trade_dates(start_date=str(start), end_date=str(end))
        df = _fetch(rs)
        trading = df[df["is_trading_day"] == "1"]
        return [d.date() for d in pd.to_datetime(trading["calendar_date"])]

    def get_market_snapshot(self, day: date) -> pd.DataFrame:
        """全市场当日日线（baostock 0.9.3 的 query_daily_history_k_AStock）。

        0 行 = error_code 0 的真空结果（当天未发布），**原样返回空表**由调用方分支；
        非 0 错误码由 _fetch 抛 BaostockError。校验不过抛 SnapshotInvalid。
        只保留首页要用的五列并转类型：baostock 全是字符串，`"1" == 1` 恒 False，
        不转的话 `tradestatus == 1` 会静默得到空表、所有分母变 0。
        """
        df = _fetch(bs.query_daily_history_k_AStock(date=str(day)))
        if df.empty:
            return pd.DataFrame(columns=SNAPSHOT_COLUMNS)
        if (df["date"] != str(day)).any():
            raise SnapshotInvalid(f"{day} 的快照里混有别的日期：{sorted(set(df['date']) - {str(day)})}")
        if len(df) < SNAPSHOT_MIN_ROWS:
            raise SnapshotInvalid(f"{day} 的快照只有 {len(df)} 行，少于 {SNAPSHOT_MIN_ROWS}（可能入库中途）")
        if df["code"].duplicated().any():
            raise SnapshotInvalid(f"{day} 的快照代码重复")
        if not df["code"].str.match(r"^(sh|sz)\.").all():
            raise SnapshotInvalid(f"{day} 的快照有非 sh./sz. 前缀的代码")
        out = pd.DataFrame({
            "code": df["code"].astype(str),
            "close": pd.to_numeric(df["close"], errors="coerce"),
            "preclose": pd.to_numeric(df["preclose"], errors="coerce"),
            "tradestatus": pd.to_numeric(df["tradestatus"], errors="coerce").fillna(0).astype(int),
            "isST": pd.to_numeric(df["isST"], errors="coerce").fillna(0).astype(int),
        })
        trading = out["tradestatus"] == 1
        if (out.loc[trading, "preclose"] <= 0).any() or out.loc[trading, "close"].isna().any():
            raise SnapshotInvalid(f"{day} 的快照里交易中的行有无效价格")
        pct = pd.to_numeric(df["pctChg"], errors="coerce")
        calc = (out["close"] / out["preclose"] - 1) * 100
        bad = trading & pct.notna() & ((pct - calc).abs() > 1e-3)
        if bad.any():
            raise SnapshotInvalid(f"{day} 的快照 pctChg 与 close/preclose 对不上：{out.loc[bad, 'code'].head(3).tolist()}")
        return out
