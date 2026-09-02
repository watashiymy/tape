# tests/test_scan_meta.py — v0.2.4 扫描范围记录与产物隔离（设计 §2 / §4）
#
# 要防的是一次已经发生过的**静默数据丢失**：产物文件名只有日期，`--limit N` 的试跑
# 直接覆盖同一天的全量扫描结果。本地 5 份扫描 CSV 里 3 份已经这么没了
# （08-24 原本 86 条信号、08-27 原本全量），而 output/ 不在版本控制内，找不回来。
#
# 两条防线各有各的测试：
#   1. `scan_csv_path` —— 路径层面的隔离（不靠约定，靠文件名）；
#   2. `ScanMeta` 伴生文件 —— 每份产物记住自己扫了多少只。
# 第三节是面板选文件的优先级（同一天全量优先于试跑）与徽标文案。
from datetime import date, datetime
from pathlib import Path

import pytest

from quant.signal import scan_meta
from quant.signal.scan_meta import ScanMeta

DAY = date(2026, 8, 27)
SKIPPED = {"stale": 2, "insufficient_history": 0, "is_st": 0,
           "low_liquidity": 534, "no_signal": 2388}


def _meta(**over) -> ScanMeta:
    """一份"全量扫了 3010 只"的 meta；各用例只改自己关心的字段。"""
    fields = dict(date=DAY, scanned=3010, pool_total=3010, limit=None, signals=86,
                  skipped=dict(SKIPPED), failed=0, elapsed_s=1258.0,
                  started_at=datetime(2026, 8, 27, 18, 32, 11))
    return ScanMeta(**{**fields, **over})


# ================================================================ scan_csv_path

def test_a_full_scan_keeps_the_plain_dated_filename(tmp_path):
    """全量产物的名字一个字不变：面板、README、用户的既有习惯都指着它。"""
    assert scan_meta.scan_csv_path(DAY, None, tmp_path) == tmp_path / "2026-08-27.csv"


def test_a_limited_run_writes_to_its_own_file(tmp_path):
    """`--limit 3` 必须落在**另一个路径**上。这是本次的核心：
    隔离不能靠"记得别在有全量结果的那天试跑"这种约定，只能靠文件系统。"""
    assert scan_meta.scan_csv_path(DAY, 3, tmp_path) == tmp_path / "2026-08-27_limit3.csv"


def test_two_different_limits_do_not_share_a_file(tmp_path):
    """不同规模的试跑也互不覆盖；相同规模重跑仍覆盖自己（那是同一件事重做）。"""
    assert scan_meta.scan_csv_path(DAY, 30, tmp_path) != \
        scan_meta.scan_csv_path(DAY, 300, tmp_path)
    assert scan_meta.scan_csv_path(DAY, 30, tmp_path) == \
        scan_meta.scan_csv_path(DAY, 30, tmp_path)


def test_the_path_is_pure_and_touches_no_disk(tmp_path):
    """纯函数：算路径不许建目录、不许写文件（调用方决定什么时候落盘）。"""
    scan_meta.scan_csv_path(DAY, 3, tmp_path / "nope")
    assert not (tmp_path / "nope").exists()


def test_the_meta_file_sits_beside_its_csv(tmp_path):
    """伴生文件按 CSV 路径推出来，两者必须一一对应——
    试跑的 meta 落到全量的 meta 上，就等于换个文件继续覆盖。"""
    full = scan_meta.scan_csv_path(DAY, None, tmp_path)
    trial = scan_meta.scan_csv_path(DAY, 3, tmp_path)
    assert scan_meta.meta_path(full) == tmp_path / "2026-08-27.meta.json"
    assert scan_meta.meta_path(trial) == tmp_path / "2026-08-27_limit3.meta.json"


# ================================================================ ScanMeta 往返

def test_a_saved_meta_round_trips_field_for_field(tmp_path):
    """每个字段都要保真：日期、只数、limit、跳过明细、耗时、起始时刻。
    少一个都会让"这趟到底扫了什么"重新变成猜谜。"""
    csv = scan_meta.scan_csv_path(DAY, None, tmp_path)
    scan_meta.save_meta(_meta(), csv)

    got = scan_meta.load_meta(csv)

    assert got == _meta()
    assert got.date == DAY and got.scanned == 3010 and got.limit is None
    assert got.skipped == SKIPPED
    assert got.started_at == datetime(2026, 8, 27, 18, 32, 11)


def test_a_limited_run_records_its_limit(tmp_path):
    csv = scan_meta.scan_csv_path(DAY, 3, tmp_path)
    scan_meta.save_meta(_meta(scanned=3, pool_total=3010, limit=3, signals=0), csv)

    got = scan_meta.load_meta(csv)

    assert (got.limit, got.scanned, got.pool_total) == (3, 3, 3010)
    assert got.is_full is False


def test_is_full_is_derived_not_taken_on_faith(tmp_path):
    """`is_full` 是 scanned == pool_total 算出来的，不是磁盘上那个布尔值。
    JSON 是手工改得动的普通文本，信它就等于允许一份 3 只票的试跑自称全量。"""
    csv = scan_meta.scan_csv_path(DAY, 3, tmp_path)
    scan_meta.save_meta(_meta(scanned=3, pool_total=3010, limit=3), csv)
    path = scan_meta.meta_path(csv)
    path.write_text(path.read_text(encoding="utf-8").replace('"is_full": false',
                                                             '"is_full": true'),
                    encoding="utf-8")

    assert scan_meta.load_meta(csv).is_full is False


def test_the_json_is_human_readable_with_the_documented_keys(tmp_path):
    """这份文件是给人看的（出问题时第一手证据），键名与设计 §2.2 一致。"""
    import json

    csv = scan_meta.scan_csv_path(DAY, None, tmp_path)
    scan_meta.save_meta(_meta(), csv)

    data = json.loads(scan_meta.meta_path(csv).read_text(encoding="utf-8"))

    assert set(data) == {"date", "scanned", "pool_total", "limit", "is_full",
                         "signals", "skipped", "failed", "elapsed_s", "started_at"}
    assert data["date"] == "2026-08-27" and data["is_full"] is True


def test_save_creates_the_parent_directory(tmp_path):
    csv = scan_meta.scan_csv_path(DAY, None, tmp_path / "output" / "scan")
    scan_meta.save_meta(_meta(), csv)
    assert scan_meta.meta_path(csv).exists()


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path):
    """与 cache.py / symbols.py 同一模式（tmp + os.replace）：
    收尾时按 Ctrl-C 不许留下半截 JSON，那会让下一次读盘撞上"文件损坏"。"""
    csv = scan_meta.scan_csv_path(DAY, None, tmp_path)
    scan_meta.save_meta(_meta(), csv)
    assert list(tmp_path.glob("*.tmp")) == []


def test_a_missing_meta_is_none_not_an_error(tmp_path):
    """v0.2.4 之前的老产物没有 meta。这不是错误，是"范围未知"——
    读不到就说读不到（渲染层据此显示徽标），不许猜。"""
    assert scan_meta.load_meta(scan_meta.scan_csv_path(DAY, None, tmp_path)) is None


def test_a_corrupt_meta_fails_loudly_with_the_path(tmp_path):
    """损坏文件必须响亮报错并带上路径 + 自愈办法（沿用 cache.py/symbols.py 的约定）。
    静默降级成 None 会把"文件坏了"显示成"范围未知"，那个坏文件能躺几个月没人发现。"""
    csv = scan_meta.scan_csv_path(DAY, None, tmp_path)
    scan_meta.save_meta(_meta(), csv)
    scan_meta.meta_path(csv).write_text("{不是 json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="损坏") as e:
        scan_meta.load_meta(csv)
    assert str(scan_meta.meta_path(csv)) in str(e.value)


def test_a_meta_missing_required_keys_is_corrupt_too(tmp_path):
    """半截 JSON（能 parse，但缺 scanned）比语法错误更危险：
    补个默认值就等于编造一个扫描范围。缺键一律当损坏。"""
    csv = scan_meta.scan_csv_path(DAY, None, tmp_path)
    scan_meta.save_meta(_meta(), csv)
    scan_meta.meta_path(csv).write_text('{"date": "2026-08-27"}', encoding="utf-8")

    with pytest.raises(RuntimeError, match="损坏"):
        scan_meta.load_meta(csv)


# ================================================================ 面板选文件的优先级
#
# 同一天既有全量又有试跑时**优先显示全量**（用户关心的是真结果）；
# 只有试跑才显示试跑，并明确标注。设计 §3 要求这条优先级有测试钉住。

def _touch(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("date,symbol\n", encoding="utf-8")
    return p


def test_no_scan_files_at_all_is_none(tmp_path):
    assert scan_meta.latest_scan(tmp_path) is None
    assert scan_meta.latest_scan(tmp_path / "does-not-exist") is None


def test_only_a_full_scan_picks_it(tmp_path):
    full = _touch(tmp_path, "2026-08-27.csv")
    assert scan_meta.latest_scan(tmp_path) == full


def test_only_a_trial_run_picks_the_trial(tmp_path):
    """那天只试跑过，就老实显示试跑的结果——没有全量可显示，藏起来更糟。"""
    trial = _touch(tmp_path, "2026-08-27_limit3.csv")
    assert scan_meta.latest_scan(tmp_path) == trial


def test_a_full_scan_wins_over_a_trial_on_the_same_day(tmp_path):
    """核心优先级。注意朴素的 `sorted(files, reverse=True)[0]` 恰好选**错**：
    ASCII 里 '_'(0x5F) > '.'(0x2E)，"2026-08-27_limit3.csv" 排在
    "2026-08-27.csv" 前面，于是面板永远显示那份 3 只票的试跑。"""
    _touch(tmp_path, "2026-08-27_limit3.csv")
    full = _touch(tmp_path, "2026-08-27.csv")
    _touch(tmp_path, "2026-08-27_limit300.csv")

    assert scan_meta.latest_scan(tmp_path) == full


def test_a_newer_trial_still_beats_an_older_full_scan(tmp_path):
    """优先级只在**同一天内**生效：昨天的全量不该压住今天的扫描结果，
    日期永远是第一顺位（标题上带着日期，用户看得见）。"""
    _touch(tmp_path, "2026-08-26.csv")
    newer = _touch(tmp_path, "2026-08-27_limit3.csv")

    assert scan_meta.latest_scan(tmp_path) == newer


# ================================================================ 范围徽标文案

def test_a_full_scan_badge_says_how_many_were_scanned():
    text, tip = scan_meta.scope_badge(_meta())
    assert text == "全量 3010 只"
    assert "3010" in tip


def test_a_trial_badge_says_it_is_a_trial_and_how_small():
    """"试跑 3 只"这五个字就是本次要买的东西：面板显示"当日无信号"时，
    用户得一眼看出这是一次 3 只票的冒烟测试，不是全市场真没机会。"""
    text, tip = scan_meta.scope_badge(_meta(scanned=3, pool_total=3010, limit=3,
                                            signals=0))
    assert text == "试跑 3 只"
    assert "3010" in tip and "limit" in tip


def test_no_meta_says_unknown_and_explains_why():
    """老产物：不报错、不猜、不编。诚实优于编造。"""
    text, tip = scan_meta.scope_badge(None)
    assert text == "范围未知"
    assert "全量" in tip and "试跑" in tip, "tooltip 没说清「未知」未知在哪儿"


# ================================================================ 取数失败必须摆到台面上（v0.5.0）

def test_judged_is_attempts_minus_failures():
    """`scanned` 是**尝试数**（写 meta 时传的是 total），失败的票没进策略判定。"""
    assert _meta(scanned=3010, failed=800).judged == 2210
    assert _meta().judged == 3010                    # 零失败时两者相等


def test_a_full_scan_with_failures_does_not_get_to_call_itself_clean():
    """本条钉住的就是那个「不报错但结论错」：一趟 3010 只里失败 800 只，
    旧文案照样写「全量 3010 只」+ tooltip「扫满了全部 3010 只」，而页面上同时
    挂着「今日无新信号」——用户据此以为全市场今天没机会，实际四分之一没看。"""
    text, tip = scan_meta.scope_badge(_meta(scanned=3010, failed=800, signals=0))

    assert "800" in text, f"徽标没提失败数：{text!r}"
    assert "2210" in tip, "tooltip 没说真正有结论的是多少只"
    assert "不等于没信号" in tip, "没说清失败的票意味着什么"
    assert text.startswith("全量 3010 只"), \
        "有失败也仍是一次全量扫描——降级成「试跑」是另一种假话（它不是 --limit 试跑）"


def test_a_trial_run_with_failures_says_so_too():
    """试跑那条路径同样要报失败数：两条分支各写各的，最容易漏掉一条。"""
    text, tip = scan_meta.scope_badge(_meta(scanned=100, pool_total=3010, limit=100,
                                            failed=7, signals=0))
    assert text.startswith("试跑 100 只") and "7" in text
    assert "93" in tip


def test_is_full_still_means_the_target_was_the_whole_pool():
    """`is_full` 的判据刻意不动：它说的是"这趟的目标是不是整个池子"，
    不是"每只都拿到了结论"。混为一谈会让失败 800 只的全量扫描显示成「试跑」。"""
    assert _meta(scanned=3010, pool_total=3010, failed=800).is_full is True
