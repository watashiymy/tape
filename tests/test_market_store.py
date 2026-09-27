import json
from datetime import date, timedelta

import pandas as pd
import pytest

from quant.market import store
from quant.market.session import Calendar

D = lambda s: date.fromisoformat(s)


def _snap(n=3):
    return pd.DataFrame({"code": [f"sh.60000{i}" for i in range(n)],
                         "close": [1.0] * n, "preclose": [1.0] * n,
                         "tradestatus": [1] * n, "isST": [0] * n})


def test_layout(tmp_path):
    ms = store.MarketStore(tmp_path / "data" / "market")
    assert ms.daily_dir == tmp_path / "data" / "market" / "daily"
    assert ms.breadth_path.name == "breadth.parquet" and ms.gaps_path.name == "gaps.json"


def test_snapshot_roundtrip_is_atomic_and_lists_by_date(tmp_path):
    ms = store.MarketStore(tmp_path)
    ms.save_snapshot(D("2026-09-22"), _snap())
    ms.save_snapshot(D("2026-09-23"), _snap())
    (ms.daily_dir / "notes.txt").write_text("x")
    (ms.daily_dir / "2026-09-24.parquet.tmp").write_text("half")
    assert ms.snapshot_dates() == [D("2026-09-22"), D("2026-09-23")], "只认文件名严格是日期的 .parquet"
    assert list(ms.load_snapshot(D("2026-09-22")).columns) == list(_snap().columns)


def test_snapshot_dates_skips_filenames_that_look_like_dates_but_are_not(tmp_path):
    ms = store.MarketStore(tmp_path)
    ms.save_snapshot(D("2026-09-22"), _snap())
    (ms.daily_dir / "2026-13-45.parquet").write_text("garbage")   # 13 月 45 日：不是合法日期
    assert ms.snapshot_dates() == [D("2026-09-22")]


def test_snapshot_rows_and_neighbours(tmp_path):
    ms = store.MarketStore(tmp_path)
    ms.save_snapshot(D("2026-09-22"), _snap(5))
    ms.save_snapshot(D("2026-09-24"), _snap(3))
    assert ms.snapshot_rows(D("2026-09-22")) == 5
    assert ms.neighbour_rows(D("2026-09-23")) == 3          # 前 5、后 3 → 取小
    assert ms.neighbour_rows(D("2026-09-25")) == 3          # 只有前一份
    assert store.MarketStore(tmp_path / "nope").neighbour_rows(D("2026-09-23")) is None


def test_clean_tmp_covers_both_directories(tmp_path):
    ms = store.MarketStore(tmp_path)
    ms.daily_dir.mkdir(parents=True)
    (ms.daily_dir / "a.parquet.tmp").write_text("x")
    (ms.root / "breadth.parquet.abc.tmp").write_text("x")
    (ms.root / "keep.json").write_text("{}")
    assert ms.clean_tmp() == 2
    assert (ms.root / "keep.json").exists()


def test_calendar_roundtrip_and_view(tmp_path):
    ms = store.MarketStore(tmp_path)
    days = pd.date_range("2026-09-01", "2026-09-30").date
    trading = {d for d in days if d.weekday() < 5 and d != D("2026-09-25")}
    ms.save_calendar(days, trading)
    cal = ms.load_calendar()
    assert isinstance(cal, Calendar) and cal.first == D("2026-09-01") and cal.last == D("2026-09-30")
    assert cal.is_trading(D("2026-09-24")) and not cal.is_trading(D("2026-09-25"))
    assert ms.load_calendar() is not None and store.MarketStore(tmp_path / "nope").load_calendar() is None


def test_load_calendar_empty_returns_none_instead_of_raising(tmp_path):
    ms = store.MarketStore(tmp_path)
    ms.save_calendar([], [])
    assert ms.load_calendar() is None


def test_calendar_completeness_rules():
    days = list(pd.date_range("2025-05-01", "2026-11-23").date)   # ≈ today−500 .. today+60
    trading = frozenset(d for d in days if d.weekday() < 5)
    today = D("2026-09-24")
    assert store.calendar_complete(days, trading, today, last_done=D("2026-09-23"), need=310) is None
    gap = [d for d in days if d != D("2026-01-15")]
    assert "缺" in store.calendar_complete(gap, trading, today, D("2026-09-23"), 310)
    short = [d for d in days if d >= D("2026-06-01")]
    assert "310" in store.calendar_complete(short, trading, today, D("2026-09-23"), 310)
    ends_early = [d for d in days if d < today]
    assert "末日" in store.calendar_complete(ends_early, trading, today, D("2026-09-23"), 310)


def test_gaps_record_and_promote(tmp_path):
    ms = store.MarketStore(tmp_path)
    assert ms.permanent_gaps() == set()
    ms.record_failure(D("2026-08-14"), failed_on=D("2026-09-24"))
    ms.record_failure(D("2026-08-14"), failed_on=D("2026-09-24"))       # 同一天重试多次只记一次
    assert ms.permanent_gaps() == set()
    ms.record_failure(D("2026-08-14"), failed_on=D("2026-09-25"))
    assert ms.permanent_gaps() == {D("2026-08-14")}
    doc = json.loads(ms.gaps_path.read_text())
    assert doc["2026-08-14"] == {"failed_on": ["2026-09-24", "2026-09-25"], "permanent": True}


def test_gaps_corrupt_top_level_shape_is_not_silently_wiped(tmp_path):
    """gaps.json 如果被写坏成一个顶层列表（不是「日期 -> {failed_on:[...]}」的字典），
    permanent_gaps() 不能悄悄当成空注册表（那会让任务重新去抓明知会失败的日子），
    但也不能让页面因为这个崩掉；record_failure 则必须炸，让任务失败得响亮。"""
    ms = store.MarketStore(tmp_path)
    ms.root.mkdir(parents=True, exist_ok=True)
    ms.gaps_path.write_text("[1, 2, 3]", encoding="utf-8")
    assert ms.permanent_gaps() == set()
    with pytest.raises(RuntimeError):
        ms.record_failure(D("2026-08-14"), failed_on=D("2026-09-24"))


def test_retention_deletes_only_dated_parquets_before_the_window(tmp_path):
    ms = store.MarketStore(tmp_path)
    for s in ("2026-01-02", "2026-06-01", "2026-09-23"):
        ms.save_snapshot(D(s), _snap())
    (ms.daily_dir / "readme.parquet").write_text("not a date")
    (ms.daily_dir / "2026-01-02.parquet.bak").write_text("x")
    removed = ms.apply_retention(window_first=D("2026-06-01"))
    assert removed == [D("2026-01-02")]
    assert ms.snapshot_dates() == [D("2026-06-01"), D("2026-09-23")]
    assert (ms.daily_dir / "readme.parquet").exists() and (ms.daily_dir / "2026-01-02.parquet.bak").exists()


def _breadth_row(**over):
    from quant.market import breadth
    row = {c: [0] for c in breadth.COLUMNS}
    row["date"] = [D("2026-09-23")]
    row.update(over)
    return pd.DataFrame(row)


def test_breadth_table_roundtrip(tmp_path):
    ms = store.MarketStore(tmp_path)
    df = _breadth_row(above20=[1], n20=[2])
    ms.save_breadth(df)
    assert ms.load_breadth().equals(df)
    assert store.MarketStore(tmp_path / "nope").load_breadth() is None


def test_breadth_table_missing_a_column_reads_as_none(tmp_path):
    """spec §5.5「任何一样读失败就逐块降级」：列不全的表（旧版本写的、手工改坏的）当读不出来，
    不抛异常——页面拿它去 `last["n20"]` 会直接 KeyError 崩页。"""
    ms = store.MarketStore(tmp_path)
    ms.save_breadth(pd.DataFrame({"date": [D("2026-09-23")], "above20": [1], "n20": [2]}))
    assert ms.load_breadth() is None
    ms.save_breadth(_breadth_row().drop(columns=["n_hl"]))
    assert ms.load_breadth() is None
