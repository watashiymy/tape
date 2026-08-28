# tests/test_run_market_scan.py — v0.1.1 §3.4 入口脚本的可离线部分：空策略守卫、单票重试
# v0.2.3 追加：全市场清单的落盘与复用（见文件末尾一节）
import importlib.util
import shutil
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from quant.data import symbols
from tests.conftest import make_bars

ROOT = Path(__file__).resolve().parent.parent
REAL_CONFIG = ROOT / "config" / "settings.yaml"

_SPEC = importlib.util.spec_from_file_location(
    "run_market_scan", Path(__file__).resolve().parent.parent / "scripts" / "run_market_scan.py")
run_market_scan = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(run_market_scan)


def test_empty_strategy_config_aborts_before_network():
    """空策略表必须在联网前退出：3200 只白抓一遍后输出一张空表是最贵的静默失败。"""
    with pytest.raises(SystemExit) as e:
        run_market_scan.require_strategies({})
    assert "strategies" in str(e.value)


def test_valid_strategy_config_builds_strategies():
    got = run_market_scan.require_strategies({"ma_cross": {"fast": 20, "slow": 60}})
    assert [s.name for s in got] == ["ma_cross"]


class FlakyService:
    """第 fail_times 次调用前都抛错的假 DataService。"""

    def __init__(self, fail_times):
        self.fail_times, self.calls = fail_times, 0

    def get_bars(self, symbol, start, end=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError(f"boom #{self.calls}")
        return f"bars:{symbol}", []


def test_fetch_with_retry_recovers_from_single_failure():
    svc = FlakyService(fail_times=1)
    assert run_market_scan.fetch_with_retry(svc, "600000", None, None) == ("bars:600000", [])
    assert svc.calls == 2


def test_fetch_with_retry_gives_up_after_second_failure():
    """重试**一次**：第二次仍失败必须抛给调用方计数，不能无限重试卡死全场扫描。"""
    svc = FlakyService(fail_times=2)
    with pytest.raises(ConnectionError):
        run_market_scan.fetch_with_retry(svc, "600000", None, None)
    assert svc.calls == 2


# ---------------------------------------------------------------------------
# 预估耗时口径（评审阻塞 1 的回归守卫）
#
# 曾犯过的方法论错误：把 get_all_symbols（每次扫描固定一次、约 11500 行分页拉取、
# 实测 2-4 分钟）的固定开销摊进每票速率，得出"3.3-3.9 秒/只、全量约 3 小时"。
# 2026-08-24 实测拆解：固定开销 133s；热缓存 30 只中位 0.51s/只、均值 0.88s/只；
# 冷缓存抽样 0.9-2.5s/只。合理口径 = 固定 2-4 分钟 + 每票 0.5-2 秒 ≈ 全量 0.5-2 小时。
# ---------------------------------------------------------------------------

_README = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")


_DOCS = [(run_market_scan.__doc__, "docstring"), (_README, "README")]


@pytest.mark.parametrize("doc,label", _DOCS, ids=[l for _, l in _DOCS])
def test_timing_estimate_not_amortized(doc, label):
    """耗时文案不得复现"固定开销摊进每票速率"的错误结论。"""
    # "3.3-3.9 秒/只"两种连字符写法都要挡；不裸查 "3.3"（会误伤将来的 §3.3 引用）
    for wrong in ("约 3 小时", "3.3-3.9", "3.3–3.9"):
        assert wrong not in doc, f"{label} 仍含错误耗时结论 {wrong!r}"


@pytest.mark.parametrize("doc,label", _DOCS, ids=[l for _, l in _DOCS])
def test_timing_estimate_separates_fixed_overhead(doc, label):
    """耗时文案必须把固定开销与每票速率分开表述（防止错误口径回潮）。"""
    assert "固定开销" in doc, f"{label} 未区分固定开销与每票速率"


def test_readme_cache_window_claim_matches_scan_config():
    """扫描票缓存的是 400 自然日窗口（scan.history_days），不是 10 年——
    "省的是不重拉 10 年历史"只对回测 universe 票成立，README 曾写错。"""
    assert "省的是不重拉 10 年历史" not in _README


# ---------------------------------------------------------------------------
# v0.2.3 全市场清单的落盘与复用
#
# `provider.get_all_symbols()` 每轮固定 2-4 分钟（约 11500 行分页），过去拉完即丢。
# 现在拉完就存 data/symbols.parquet，7 天内直接复用。断言的重点：
#   1. **存的必须是完整清单**——`--limit` 是筛选之后截取的，一次 `--limit 30`
#      若把缓存污染成只有 30 只，面板的名称与候选清单就会跟着只剩 30 只，
#      而且不会有任何报错（正是本项目最忌讳的静默失败）；
#   2. 新鲜就不联网，且**日志要说出来**（用户得知道这次为什么快了）；
#   3. 不新鲜 / --refresh-symbols 要真的重拉；
#   4. 清单文件损坏必须响亮失败，不许退化成"每次都重拉"。
#
# 这一节直接跑 main()：`--limit` 的截取发生在 main 里，只测辅助函数的话，
# "存完整清单"这条根本没被覆盖（在 main 里把 save 挪到 head() 之后照样全绿）。
# ---------------------------------------------------------------------------

FAKE_ROWS = [("000333", "美的集团"), ("600036", "招商银行"),
             ("600519", "贵州茅台"), ("601318", "中国平安")]
BASE_DAY = date(2026, 8, 26)


class FakeProvider:
    """BaostockProvider 的替身。真的会登录并拉 11500 行（2-4 分钟），离线测试里一次都不许。"""

    calls = 0
    fail_with: Exception | None = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_trade_calendar(self, start, end):
        return [BASE_DAY]

    def get_all_symbols(self, as_of):
        type(self).calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        return pd.DataFrame(FAKE_ROWS, columns=["symbol", "name"])


class FakeService:
    """DataService 的替身：返回一根 bar，于是每只票都判为 insufficient_history。

    扫描本身不是这一节要测的东西（tests/test_market_scan.py 管那个），
    这里只要 main() 能整条跑完、把清单存下来。
    """

    def __init__(self, provider, cache):
        self.provider = provider

    def get_bars(self, symbol, start, end=None):
        return make_bars([dict(date=str(BASE_DAY), open=10, high=10, low=10,
                               close=10, volume=1000, amount=1e8)]), []


def _run_scan(tmp_path, monkeypatch, argv: list[str]) -> Path:
    """在 tmp_path 里整条跑一次 main()，返回清单文件路径。

    三处落点全部改道到 tmp_path：清单文件、扫描 CSV、行情缓存目录——
    真实的 data/ 与 output/ 一个字节都不许动。
    """
    config = tmp_path / "settings.yaml"
    shutil.copyfile(REAL_CONFIG, config)          # 真配置的只读副本（策略段要真的能建出策略）
    symbols_path = tmp_path / "data" / "symbols.parquet"
    FakeProvider.calls = 0
    FakeProvider.fail_with = None
    monkeypatch.setattr(run_market_scan, "BaostockProvider", FakeProvider)
    monkeypatch.setattr(run_market_scan, "DataService", FakeService)
    monkeypatch.setattr(run_market_scan, "SYMBOLS_PATH", symbols_path)
    monkeypatch.setattr(run_market_scan, "SCAN_DIR", tmp_path / "output" / "scan")
    monkeypatch.setattr(run_market_scan, "CACHE_DIR", tmp_path / "data" / "cache")
    monkeypatch.setattr("sys.argv", ["run_market_scan.py", "--config", str(config),
                                     "--date", str(BASE_DAY), *argv])
    run_market_scan.main()
    return symbols_path


def test_the_full_listing_is_saved_not_the_limited_slice(tmp_path, monkeypatch, capsys):
    """`--limit` 在清单**筛选之后**截取。落盘必须存完整清单：
    一次 `--limit 30` 把缓存污染成 30 只的话，面板的名称列与候选清单会跟着只剩 30 只，
    而且没有任何报错——只表现为"名字又没了"。"""
    path = _run_scan(tmp_path, monkeypatch, ["--limit", "1"])

    assert "扫描池 1 只" in capsys.readouterr().out, "--limit 没生效，这条测试就没在测它"
    loaded, as_of = symbols.load_symbols(path)
    assert list(loaded["symbol"]) == [s for s, _ in FAKE_ROWS], \
        "落盘的是被 --limit 截断的清单，缓存已被污染"
    assert list(loaded["name"]) == [n for _, n in FAKE_ROWS]
    assert as_of == BASE_DAY


def test_the_listing_is_saved_on_a_normal_full_run(tmp_path, monkeypatch):
    path = _run_scan(tmp_path, monkeypatch, [])
    loaded, as_of = symbols.load_symbols(path)
    assert len(loaded) == len(FAKE_ROWS)
    assert as_of == BASE_DAY


def test_a_fresh_local_listing_is_reused_without_going_online(tmp_path, monkeypatch,
                                                              capsys):
    """本地清单新鲜（7 天内）就直接用，省下每轮 2-4 分钟。
    日志必须明确说"复用本地清单（as_of=…）"——否则用户只知道这次快了，不知道为什么，
    也就无从判断名称/候选清单是不是旧的。

    新鲜度按**真实**今天算相对天数（存 as_of=今天），不冻结时钟：
    冻结时钟要在被测模块里留一个 today() 接缝，而 is_fresh 的边界已经在
    tests/test_symbols.py 里逐天钉过了，这里只需要"确实是新鲜的"这一个事实。
    """
    saved = tmp_path / "data" / "symbols.parquet"
    symbols.save_symbols(pd.DataFrame([("600519", "贵州茅台")], columns=["symbol", "name"]),
                         date.today(), saved)

    _run_scan(tmp_path, monkeypatch, [])

    assert FakeProvider.calls == 0, "本地清单新鲜却仍然联网重拉了"
    out = capsys.readouterr().out
    assert "复用本地清单" in out, out
    assert str(BASE_DAY) in out, "没说清复用的是哪一天的清单"
    assert "扫描池 1 只" in out, "复用的清单没有真的拿去扫描"


def test_a_stale_local_listing_is_refetched_and_overwritten(tmp_path, monkeypatch):
    """8 天前的清单已经过期（新股上市/退市/改名会积累），必须重拉并覆盖。"""
    saved = tmp_path / "data" / "symbols.parquet"
    stale = date.fromordinal(date.today().toordinal() - 8)
    symbols.save_symbols(pd.DataFrame([("600519", "贵州茅台")], columns=["symbol", "name"]),
                         stale, saved)

    path = _run_scan(tmp_path, monkeypatch, [])

    assert FakeProvider.calls == 1
    loaded, as_of = symbols.load_symbols(path)
    assert len(loaded) == len(FAKE_ROWS)
    assert as_of == BASE_DAY


def test_refresh_symbols_forces_a_refetch_even_when_fresh(tmp_path, monkeypatch):
    """`--refresh-symbols` 是清单的强制开关（与回测脚本的 `--refresh` 无关，
    后者管的是行情缓存）。清单出问题时用户得有办法不删文件也能重来一次。"""
    saved = tmp_path / "data" / "symbols.parquet"
    symbols.save_symbols(pd.DataFrame([("600519", "贵州茅台")], columns=["symbol", "name"]),
                         date.today(), saved)

    path = _run_scan(tmp_path, monkeypatch, ["--refresh-symbols"])

    assert FakeProvider.calls == 1, "--refresh-symbols 没有强制重拉"
    loaded, _ = symbols.load_symbols(path)
    assert len(loaded) == len(FAKE_ROWS)


def test_refresh_symbols_is_not_the_same_switch_as_refresh(tmp_path, monkeypatch):
    """两个开关必须分得开：`--refresh` 不是本脚本的参数，误打要当场报错，
    不能被悄悄当成"刷新清单"（那会让人以为行情缓存也重拉了）。"""
    with pytest.raises(SystemExit):
        _run_scan(tmp_path, monkeypatch, ["--refresh"])


def test_a_corrupt_listing_fails_loudly_instead_of_silently_refetching(tmp_path,
                                                                      monkeypatch):
    """坏文件绝不能被当成"没有缓存"：那样每轮都白付 2-4 分钟重拉，
    而那个坏文件可以在磁盘上躺几个月没人发现。"""
    saved = tmp_path / "data" / "symbols.parquet"
    symbols.save_symbols(pd.DataFrame([("600519", "贵州茅台")], columns=["symbol", "name"]),
                         date.today(), saved)
    saved.write_bytes(saved.read_bytes()[:20])

    with pytest.raises(RuntimeError, match="损坏"):
        _run_scan(tmp_path, monkeypatch, [])
    assert FakeProvider.calls == 0, "撞上坏文件却照样联网重拉了——问题被掩盖"


def test_the_scan_still_works_when_there_is_no_local_listing(tmp_path, monkeypatch):
    """降级：没有清单文件时行为与从前完全一致（联网拉一次），不许崩。"""
    path = _run_scan(tmp_path, monkeypatch, [])
    assert FakeProvider.calls == 1
    assert path.exists(), "拉完必须落盘，否则下一轮又要重付 2-4 分钟"
