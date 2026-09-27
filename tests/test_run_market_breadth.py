import importlib.util
import re
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd
import pytest

from quant.data.baostock_provider import BaostockError, SnapshotInvalid
from quant.market import store as mstore

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "run_market_breadth.py"
SRC = SCRIPT.read_text("utf-8")


def _load():
    spec = importlib.util.spec_from_file_location("run_market_breadth", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_market_breadth"] = mod
    spec.loader.exec_module(mod)
    return mod


def _cal(today: date):
    days = [today - timedelta(days=500) + timedelta(days=i) for i in range(561)]
    return days, frozenset(d for d in days if d.weekday() < 5)


def _snap(n=5):
    return pd.DataFrame({"code": [f"sh.60000{i}" for i in range(n)], "close": [1.0] * n,
                         "preclose": [1.0] * n, "tradestatus": [1] * n, "isST": [0] * n})


class FakeProvider:
    """按日期返回快照；missing 里的日期返回空表；invalid 里的抛 SnapshotInvalid；
    partial 里的日期只返回 3 行（模拟入库中途）；session_fail_on 那天第一次抛会话错误
    （always_session_fail=True 时每次都抛，模拟重登也救不回来）。"""
    def __init__(self, days, trading, missing=(), invalid=(), partial=(), session_fail_on=None,
                 always_session_fail=False):
        self.days, self.trading = days, trading
        self.missing, self.invalid, self.partial = set(missing), set(invalid), set(partial)
        self.session_fail_on, self.always_session_fail = session_fail_on, always_session_fail
        self.calls, self.logins = [], 0

    def __enter__(self):
        self.logins += 1
        return self

    def __exit__(self, *a):
        return False

    def get_trade_calendar(self, start, end):
        return [d for d in self.trading if start <= d <= end]

    def get_market_snapshot(self, day):
        self.calls.append(day)
        if day == self.session_fail_on and (self.always_session_fail or self.calls.count(day) == 1):
            raise BaostockError("10001001", "用户未登录")
        if day in self.invalid:
            raise SnapshotInvalid(f"{day} bad")
        if day in self.missing:
            return pd.DataFrame(columns=mstore.SNAPSHOT_COLUMNS)
        if day in self.partial:
            return _snap(3)
        return _snap()


TODAY = date(2026, 9, 24)      # 周四
BAD_DAY = date(2026, 9, 3)     # 相对 TODAY 稳稳落在 30 个交易日窗口以内（离两头都留足余量）


def _run(tmp_path, provider, now_t=time(19, 0), today=TODAY, window=30):
    """其余用例不需要 310 天语义时，默认把窗口缩到 30，省掉几十次全量重算
    （每次全量 run 约 63 次）；需要真 310 语义的用例显式传 window=310。"""
    mod = _load()
    mod.WINDOW_TRADING_DAYS = window
    ms = mstore.MarketStore(tmp_path / "data" / "market")
    lines = []
    code = mod.run(lambda: provider, ms, today=today, now_t=now_t, log=lines.append)
    return code, "\n".join(lines), ms


def test_first_run_backfills_newest_first_and_recomputes_every_five_days(tmp_path):
    days, trading = _cal(TODAY)
    prov = FakeProvider(days, trading)
    code, log, ms = _run(tmp_path, prov, window=310)
    assert code == 0 and "全部完成: " in log
    assert prov.calls[0] > prov.calls[-1], "从新往旧"
    assert prov.calls[0] == TODAY, "19:00 已过就绪时刻，窗口末端是今天"
    assert len(ms.snapshot_dates()) == 310
    assert ms.load_breadth() is not None
    assert log.count("指标表已重算") >= 310 // 5


def test_before_ready_time_the_window_ends_yesterday(tmp_path):
    days, trading = _cal(TODAY)
    prov = FakeProvider(days, trading)
    _run(tmp_path, prov, now_t=time(16, 0))
    assert TODAY not in prov.calls and max(prov.calls) == date(2026, 9, 23)


def test_today_not_published_is_skipped_and_still_a_success(tmp_path):
    days, trading = _cal(TODAY)
    prov = FakeProvider(days, trading, missing={TODAY})
    code, log, ms = _run(tmp_path, prov)
    assert code == 0
    assert re.search(r"^\[1/\d+\] 2026-09-24 跳过：", log, re.M)
    assert "2026-09-24 的收盘数据还没发布" in log and "全部完成: " in log
    assert len(ms.snapshot_dates()) == 29 and TODAY not in ms.snapshot_dates()


def test_past_day_zero_rows_is_recorded_and_run_fails_but_continues(tmp_path):
    days, trading = _cal(TODAY)
    bad = BAD_DAY
    prov = FakeProvider(days, trading, missing={bad})
    code, log, ms = _run(tmp_path, prov)
    assert code != 0 and "补数失败：" in log and "全部完成" not in log
    assert prov.calls.count(bad) == 2, "重试 1 次"
    assert min(prov.calls) < bad, "记下之后继续往旧补"
    assert ms.permanent_gaps() == set() and bad.isoformat() in ms.gaps_path.read_text()
    assert ms.load_breadth() is not None, "失败也重算"


def test_second_failure_on_another_day_makes_it_permanent_and_stops_retrying(tmp_path):
    days, trading = _cal(TODAY)
    bad = BAD_DAY
    _run(tmp_path, FakeProvider(days, trading, missing={bad}))
    prov2 = FakeProvider(days, trading, missing={bad})
    code, log, ms = _run(tmp_path, prov2, today=TODAY + timedelta(days=1))
    assert ms.permanent_gaps() == {bad} and code != 0
    prov3 = FakeProvider(days, trading, missing={bad})
    code3, log3, _ = _run(tmp_path, prov3, today=TODAY + timedelta(days=4))
    assert bad not in prov3.calls and code3 == 0, "永久缺口不再重试、不再让任务失败"


def test_session_error_relogs_in_and_retries(tmp_path):
    days, trading = _cal(TODAY)
    prov = FakeProvider(days, trading, session_fail_on=date(2026, 9, 22))
    code, log, ms = _run(tmp_path, prov)
    assert code == 0 and prov.logins >= 2 and date(2026, 9, 22) in ms.snapshot_dates()


def test_session_error_exhausted_stops_without_recording_a_gap(tmp_path):
    """会话失效是本机会话的问题，不是数据源缺数：重登 2 次仍失败 → 停止补数、重算、以失败结束，
    **不进 gaps.json**（否则两个自然日各失败一次就会把一个有数据的日子标成永久缺口）。"""
    days, trading = _cal(TODAY)
    bad = date(2026, 9, 22)
    prov = FakeProvider(days, trading, session_fail_on=bad, always_session_fail=True)
    code, log, ms = _run(tmp_path, prov)
    assert code != 0 and "会话" in log and "全部完成" not in log
    assert not ms.gaps_path.exists() or "2026-09-22" not in ms.gaps_path.read_text()
    assert min(prov.calls) == bad, "停止补数：没有再往旧请求"
    assert ms.load_breadth() is not None, "已存的日子照样重算"


def test_partial_snapshot_is_rejected_by_the_98_percent_rule(tmp_path):
    """当天入库中途只拿到一部分（相邻快照 5 行、这份 3 行）→ 当「当天未发布」处理，不存。
    规则比的是**相邻已存快照**，所以先放一份 9-23 进去；全新目录的第一份没有相邻件，规则天然不生效。"""
    days, trading = _cal(TODAY)
    mstore.MarketStore(tmp_path / "data" / "market").save_snapshot(date(2026, 9, 23), _snap())
    prov = FakeProvider(days, trading, partial={TODAY})
    code, log, ms = _run(tmp_path, prov)
    assert code == 0 and TODAY not in ms.snapshot_dates()
    assert re.search(r"2026-09-24 跳过：.*98%", log)


def test_window_can_be_shrunk_for_fast_tests(tmp_path):
    """其余用例不需要 310 天语义时可把窗口缩到 30（`_run` 默认就是 30），省掉几十次全量重算。"""
    days, trading = _cal(TODAY)
    code, _, ms = _run(tmp_path, FakeProvider(days, trading))
    assert code == 0 and len(ms.snapshot_dates()) == 30


def test_incremental_run_only_fetches_the_gap(tmp_path):
    days, trading = _cal(TODAY)
    _run(tmp_path, FakeProvider(days, trading))
    prov = FakeProvider(days, trading)
    # 次日 16:00：就绪时刻前，窗口末端仍是 9-24（已补）→ 一天都不请求
    code, log, _ = _run(tmp_path, prov, today=date(2026, 9, 25), now_t=time(16, 0))
    assert prov.calls == [] and "待补 0 天" in log and code == 0


def test_retention_runs_even_when_fetching_failed(tmp_path):
    days, trading = _cal(TODAY)
    ms = mstore.MarketStore(tmp_path / "data" / "market")
    ms.save_snapshot(date(2025, 1, 6), _snap())            # 窗口外的旧文件
    prov = FakeProvider(days, trading, missing={BAD_DAY})
    code, _, ms = _run(tmp_path, prov)
    assert code != 0 and date(2025, 1, 6) not in ms.snapshot_dates()


def test_incomplete_calendar_aborts_without_deleting(tmp_path):
    days, trading = _cal(TODAY)
    # 只留最近 20 个自然日（约 15 个交易日），比默认窗口 30 还短，才能测出「日历不完整」
    short = [d for d in days if d >= TODAY - timedelta(days=20)]
    ms = mstore.MarketStore(tmp_path / "data" / "market")
    ms.save_snapshot(date(2025, 1, 6), _snap())
    prov = FakeProvider(short, frozenset(d for d in short if d.weekday() < 5))
    code, log, ms = _run(tmp_path, prov)
    assert code != 0 and "日历" in log and date(2025, 1, 6) in ms.snapshot_dates()


def test_script_never_prints_the_saved_marker_anywhere():
    assert "已保存:" not in SRC, "process.DONE_MARKERS 用子串匹配，这个前缀会把失败判成成功"


def test_tmp_files_are_cleaned_at_start(tmp_path):
    ms = mstore.MarketStore(tmp_path / "data" / "market")
    ms.daily_dir.mkdir(parents=True)
    (ms.daily_dir / "x.parquet.tmp").write_text("x")
    (ms.root / "breadth.parquet.y.tmp").write_text("x")
    days, trading = _cal(TODAY)
    _run(tmp_path, FakeProvider(days, trading))
    assert not list(ms.root.glob("*.tmp")) and not list(ms.daily_dir.glob("*.tmp"))
