# tests/test_run_market_scan.py — v0.1.1 §3.4 入口脚本的可离线部分：空策略守卫、单票重试
# v0.2.3 追加：全市场清单的落盘与复用
# v0.2.4 追加：就绪闸门（交易日校验 + 数据就绪校验，见文件末尾一节）
import importlib.util
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from quant.data import symbols
from quant.signal import scan_meta
from quant.data.pipeline import prepare_bars
from quant.signal.market_scan import MIN_BARS
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
    trade_days: tuple[date, ...] = (BASE_DAY,)   # v0.2.4：交易日历（闸门一查的就是它）

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_trade_calendar(self, start, end):
        # 真 provider 按区间查（query_trade_dates 的 start/end），替身也照做：
        # 直接无视区间返回固定一天的话，"非交易日"这件事在测试里就不可能发生。
        return [d for d in self.trade_days if start <= d <= end]

    def get_all_symbols(self, as_of):
        type(self).calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        return pd.DataFrame(FAKE_ROWS, columns=["symbol", "name"])


#: 「历史齐全」用的 bar 数。两个边界都是有意的（v0.4.0 M3）：
#:   下界 200 —— 真配置默认开着 200 日趋势过滤，MA(200) 未成形时 gate 恒为 False，
#:     130 根（MIN_BARS，只够过 insufficient_history 那道闸门）一条 BUY 都报不出来；
#:     线上一趟扫描取 scan.history_days=400 自然日 ≈ 270 根，所以 240 与线上同量级。
#:   上界 250 —— 刻意停在 tsmom 的 250 根暖机期**之内**，这套 fixture 因此仍然只有
#:     ma_cross 一个策略报信号，下面那些"每只票恰好一条信号"的断言才继续说得准。
HEALTHY_BARS = 240


def fake_bars(end: date, n: int = 1) -> pd.DataFrame:
    """n 根工作日 bar（过 prepare_bars，与线上编排一致），最后一根落在 end。

    末根收盘 +5%：n ≥ HEALTHY_BARS 时 MA20 恰好在最后一天上穿 MA60（前面全平 →
    两线相等 → 仓位 0）且收盘站上 MA200（前面全平 → 均线就是 10.0），于是真配置的
    ma_cross 会给出一条**新 BUY**——"正常情形照常产出"这条断言得真的有信号落进
    CSV，否则它与"闸门误伤"根本区分不开。
    5% < prepare_bars 的 11% 跳变阈值，不会带出告警。
    """
    dates = pd.bdate_range(end=pd.Timestamp(end), periods=n)
    closes = [10.0] * (n - 1) + [10.5]
    rows = [dict(date=d.strftime("%Y-%m-%d"), open=c, high=c * 1.05, low=c * 0.95,
                 close=c, volume=1e6, amount=1e8) for d, c in zip(dates, closes)]
    df, _warns = prepare_bars(make_bars(rows))
    return df


class FakeService:
    """DataService 的替身：默认返回一根 bar，于是每只票都判为 insufficient_history。

    扫描本身不是这一节要测的东西（tests/test_market_scan.py 管那个），
    这里只要 main() 能整条跑完、把清单存下来。

    v0.2.4 就绪闸门那一节要摆布"数据更新到哪天""哪只票取数失败"，因此加了几个
    类级旋钮；默认值 = 从前的行为，上面几节的断言不受影响。测试一律用
    monkeypatch.setattr 改它们（自动还原，免得互相串味）。
    """

    calls = 0                                    # 取数次数：证明闸门一拦在长跑之前
    last_bar: date = BASE_DAY                    # 每只票最后一根 bar 的日期
    last_bars: dict[str, date] = {}              # 单只票覆写（部分停牌的场景）
    bars_per_symbol = 1                          # 1 → insufficient_history；≥130 → 真跑策略
    fail_symbols: tuple[str, ...] = ()           # 这些票取数抛错（进 failures 计数）

    def __init__(self, provider, cache):
        self.provider = provider

    def get_bars(self, symbol, start, end=None):
        type(self).calls += 1
        if symbol in self.fail_symbols:
            raise ConnectionError(f"取数失败: {symbol}")
        return fake_bars(self.last_bars.get(symbol, self.last_bar), self.bars_per_symbol), []


def _run_scan(tmp_path, monkeypatch, argv: list[str],
              date_arg: str | None = str(BASE_DAY)) -> Path:
    """在 tmp_path 里整条跑一次 main()，返回清单文件路径。

    三处落点全部改道到 tmp_path：清单文件、扫描 CSV、行情缓存目录——
    真实的 data/ 与 output/ 一个字节都不许动。

    date_arg=None 表示**不传** `--date`，即用户每天实际敲的那条命令
    （基准日由交易日历定）——闸门必须在这条路径上也成立。
    """
    config = tmp_path / "settings.yaml"
    shutil.copyfile(REAL_CONFIG, config)          # 真配置的只读副本（策略段要真的能建出策略）
    symbols_path = tmp_path / "data" / "symbols.parquet"
    FakeProvider.calls = 0
    FakeProvider.fail_with = None
    FakeService.calls = 0
    monkeypatch.setattr(run_market_scan, "BaostockProvider", FakeProvider)
    monkeypatch.setattr(run_market_scan, "DataService", FakeService)
    monkeypatch.setattr(run_market_scan, "SYMBOLS_PATH", symbols_path)
    monkeypatch.setattr(run_market_scan, "SCAN_DIR", tmp_path / "output" / "scan")
    monkeypatch.setattr(run_market_scan, "CACHE_DIR", tmp_path / "data" / "cache")
    monkeypatch.setattr("sys.argv", ["run_market_scan.py", "--config", str(config),
                                     *(["--date", date_arg] if date_arg else []), *argv])
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


# ---------------------------------------------------------------------------
# v0.2.4 就绪闸门（cce9659 的回归）
#
# 上一版扫描脚本"当日数据是否就绪"的唯一闸门，是 provider.get_all_symbols() 在
# 空结果时抛的 ValueError。清单落盘之后，缓存命中那条路径**根本不调用它**——
# 闸门被整条绕过：周六也能"扫描成功"，17:30 前也能"扫描成功"，输出的
# "今日无新信号" 与真正的无信号日逐字相同，跑完全量 0.5-2 小时才发现的那种。
#
# 教训写进测试结构：这一节的每条闸门断言都要在**缓存命中与联网拉取两条路径**上跑
# （上一版只测了联网那条，于是回归全绿通过）。两道闸门：
#   闸门一 交易日校验：基准日不是交易日 → 长跑之前就退（别让人等两小时才知道日期填错）；
#   闸门二 数据就绪校验：扫过的标的**全部** stale → 数据没到位，非零退出且**不写 CSV**
#          （那份空 CSV 会出现在面板「信号」页，看着像一次正常的无信号扫描）。
# ---------------------------------------------------------------------------

SATURDAY = date(2026, 8, 29)     # BASE_DAY 那一周的周六：日历上没有，绝不能扫出结果
PREV_DAY = date(2026, 8, 25)     # BASE_DAY 的前一个交易日（模拟 17:30 前：数据还停在昨天）
ALL_SOURCES = pytest.mark.parametrize("cached", [True, False],
                                      ids=["缓存命中", "联网拉取"])


def _seed_fresh_listing(tmp_path, rows=FAKE_ROWS) -> Path:
    """在 tmp_path 里放一份**新鲜**的本地清单（as_of=今天 → is_fresh 必为真）。"""
    path = tmp_path / "data" / "symbols.parquet"
    symbols.save_symbols(pd.DataFrame(list(rows), columns=["symbol", "name"]),
                         date.today(), path)
    return path


def _csvs(tmp_path) -> list[str]:
    """tmp_path 下产出的全部 CSV 文件名。查整棵树而不是查某个固定路径：
    换个文件名写出来同样是"留下了一份假的扫描结果"。"""
    return sorted(p.name for p in (tmp_path / "output").rglob("*.csv"))


def _nonzero_exit(exc: SystemExit) -> bool:
    """sys.exit(非空串) → 解释器打印该串并以 1 退出；exit(None)/exit(0) 才是"成功"。
    面板判任务成败看的就是这个退出码（runner/process.py 的 _probe）。"""
    return exc.code not in (None, 0)


# ---------- 闸门一：交易日校验 ----------

@ALL_SOURCES
def test_a_non_trading_day_is_rejected_before_the_long_scan(tmp_path, monkeypatch, cached):
    """`--date 2026-08-29`（周六）必须当场退出：周六没有行情，扫出来的任何结论都是假的。

    而且要退在**长跑之前**：全量 3013 只要 0.5-2 小时，日期填错却要等两小时才知道
    是不能接受的。断言"一票未取、清单也没去拉"就是在钉这个时序。
    """
    if cached:
        _seed_fresh_listing(tmp_path)

    with pytest.raises(SystemExit) as e:
        _run_scan(tmp_path, monkeypatch, [], date_arg=str(SATURDAY))

    assert _nonzero_exit(e.value), f"非交易日却是成功退出: {e.value.code!r}"
    assert str(SATURDAY) in str(e.value) and "不是交易日" in str(e.value), str(e.value)
    assert _csvs(tmp_path) == [], "周六竟然产出了扫描 CSV"
    assert FakeService.calls == 0, "闸门一没拦在长跑之前（已经开始逐票取数了）"
    assert FakeProvider.calls == 0, "非交易日还去拉了那 2-4 分钟的全市场清单"


# ---------- 闸门二：数据就绪校验 ----------

@ALL_SOURCES
def test_all_stale_is_data_not_ready_not_a_quiet_day(tmp_path, monkeypatch, cached, capsys):
    """17:30 前跑：扫过的票**全部**落后于基准日 = 数据没到位，不是"今日无新信号"。

    这是本项目一路在防的"不报错但结论错"：两种情况的终端输出逐字相同，
    跑完全量要 0.5-2 小时，谁也分不出刚才那趟到底有没有意义。
    """
    if cached:
        _seed_fresh_listing(tmp_path)
    monkeypatch.setattr(FakeService, "last_bar", PREV_DAY)

    with pytest.raises(SystemExit) as e:
        _run_scan(tmp_path, monkeypatch, [])

    msg, out = str(e.value), capsys.readouterr().out
    assert _nonzero_exit(e.value), f"数据未就绪却是成功退出: {e.value.code!r}"
    assert str(BASE_DAY) in msg and "17:30" in msg, f"没说清是哪天的数据没到、几点再来: {msg}"
    assert _csvs(tmp_path) == [], "数据未就绪却留下了 CSV（面板「信号」页会当成正常无信号）"
    assert "今日无新信号" not in out, '把「数据没到位」说成了「今日无新信号」'


def test_the_plain_daily_command_is_gated_too(tmp_path, monkeypatch):
    """不带 `--date`（用户每天实际敲的那条）同样要拦：基准日改由交易日历给出，
    闸门二照样成立——回归当初就是从这条命令上表现出来的。"""
    today = date.today()
    monkeypatch.setattr(FakeProvider, "trade_days", (today,))
    monkeypatch.setattr(FakeService, "last_bar", today - timedelta(days=1))
    _seed_fresh_listing(tmp_path)

    with pytest.raises(SystemExit) as e:
        _run_scan(tmp_path, monkeypatch, [], date_arg=None)

    assert _nonzero_exit(e.value)
    assert _csvs(tmp_path) == []


def test_an_existing_csv_is_not_clobbered_when_data_is_not_ready(tmp_path, monkeypatch):
    """"不写 CSV"还包括"不许覆盖已有的那份好的"：同一天早上 17:30 前手滑跑一次，
    不能把昨晚跑出来的真结果冲成空表。"""
    scan_dir = tmp_path / "output" / "scan"
    scan_dir.mkdir(parents=True)
    csv = scan_dir / f"{BASE_DAY}.csv"
    good = "date,symbol,name,strategy,close,pct_chg,amount,amount_ratio_20d\n" \
           "2026-08-26,600519,贵州茅台,ma_cross,1500.0,1.2,3.0e8,1.8\n"
    csv.write_text(good, encoding="utf-8")
    monkeypatch.setattr(FakeService, "last_bar", PREV_DAY)

    with pytest.raises(SystemExit):
        _run_scan(tmp_path, monkeypatch, [])

    assert csv.read_text(encoding="utf-8") == good, "把已有的真结果覆盖成空表了"


def test_data_not_ready_is_still_detected_when_some_symbols_failed(tmp_path, monkeypatch):
    """取数失败的票**没有结论**，不能算进分母：4 只里 1 只失败、其余 3 只全 stale，
    仍然是"数据未就绪"。朴素写法 `stale == 扫描池` 会因为那 1 只失败而漏判。"""
    monkeypatch.setattr(FakeService, "fail_symbols", (FAKE_ROWS[0][0],))
    monkeypatch.setattr(FakeService, "last_bar", PREV_DAY)

    with pytest.raises(SystemExit) as e:
        _run_scan(tmp_path, monkeypatch, [])

    assert _nonzero_exit(e.value)
    assert _csvs(tmp_path) == []


def test_every_symbol_failing_is_not_a_quiet_day_either(tmp_path, monkeypatch, capsys):
    """全部取数失败（断网/被限流）＝ 一只票都没完成判定，同样没有任何结论可言。
    此时落一份空 CSV 与"今日无新信号"又是逐字相同的输出。"""
    monkeypatch.setattr(FakeService, "fail_symbols", tuple(s for s, _ in FAKE_ROWS))

    with pytest.raises(SystemExit) as e:
        _run_scan(tmp_path, monkeypatch, [])

    assert _nonzero_exit(e.value)
    assert _csvs(tmp_path) == []
    assert "取数失败" in capsys.readouterr().out, "退出前没把失败清单打出来，没法排查"


def test_an_empty_universe_aborts_instead_of_writing_an_empty_csv(tmp_path, monkeypatch):
    """边界：扫描池为空（清单文件是空的）不能被当成"数据未就绪"，也不能"成功"跑完——
    0 只标的扫出 0 条信号是纯粹的假成功，且不该走到逐票取数那一步。"""
    _seed_fresh_listing(tmp_path, rows=[])

    with pytest.raises(SystemExit) as e:
        _run_scan(tmp_path, monkeypatch, [])

    assert _nonzero_exit(e.value)
    assert "扫描池为空" in str(e.value), str(e.value)
    assert _csvs(tmp_path) == []
    assert FakeService.calls == 0


# ---------- 正常情形：闸门不许误伤 ----------

@ALL_SOURCES
def test_a_healthy_run_still_produces_the_csv(tmp_path, monkeypatch, cached, capsys):
    """数据齐全（每只票都更新到基准日、历史够长）时行为一字不变：
    信号照常打印、CSV 照常落盘、正常退出。闸门只该拦住"没有结论"的那几种情形。"""
    if cached:
        _seed_fresh_listing(tmp_path)
    monkeypatch.setattr(FakeService, "bars_per_symbol", HEALTHY_BARS)

    _run_scan(tmp_path, monkeypatch, [])

    out = capsys.readouterr().out
    assert "全市场新 BUY 信号" in out and "已保存" in out
    assert _csvs(tmp_path) == [f"{BASE_DAY}.csv"]
    df = pd.read_csv(tmp_path / "output" / "scan" / f"{BASE_DAY}.csv", dtype={"symbol": str})
    assert list(df.columns) == run_market_scan.CSV_COLUMNS
    assert set(df["symbol"]) == {s for s, _ in FAKE_ROWS}, "有信号的票没有全部落进 CSV"
    assert "ma_cross" in set(df["strategy"])


def _scan_symbols(tmp_path) -> set[str]:
    df = pd.read_csv(tmp_path / "output" / "scan" / f"{BASE_DAY}.csv", dtype={"symbol": str})
    return set(df["symbol"])


def test_the_real_configs_trend_filter_reaches_this_entry_point(tmp_path, monkeypatch):
    """端到端：真配置里那层 200 日趋势过滤**确实**作用到了全市场扫描这个入口。

    这是设计 §2.1 末段欠下的那条行为断言。M2 时办不到：ATR 止损对"新 BUY"恒等
    （入场当根不可能被止损），所以扫描入口是否接上叠加层，行为上分辨不出，
    只能靠源码级的实参断言。趋势过滤打破了这条恒等——

    同一段行情跑两趟，唯一差别是历史长度：
      130 根（MIN_BARS，刚够过 insufficient_history）→ MA(200) 还没成形，
        gate 恒为 False → 一条 BUY 都不该有；
      240 根 → MA(200) 成形且收盘站上它 → 每只票照常报出 ma_cross 的新 BUY。
    若入口把 settings.overlays 换成 OverlaysCfg()（或压根不走 pipeline），
    第一趟会照常报出 4 条 BUY，而扫描脚本 exit 0、CSV 看着完全正常。
    """
    monkeypatch.setattr(FakeService, "bars_per_symbol", MIN_BARS)
    _run_scan(tmp_path, monkeypatch, [])
    assert _scan_symbols(tmp_path) == set(), \
        "MA(200) 暖机期内不该有任何 BUY——趋势过滤没接上这个入口"

    monkeypatch.setattr(FakeService, "bars_per_symbol", HEALTHY_BARS)
    _run_scan(tmp_path, monkeypatch, [])
    assert _scan_symbols(tmp_path) == {s for s, _ in FAKE_ROWS}, \
        "历史够长时必须照常报 BUY，否则上面那条断言测的只是「这套行情从来没信号」"


def test_a_stale_symbol_among_healthy_ones_is_a_suspension_not_an_outage(tmp_path,
                                                                         monkeypatch,
                                                                         capsys):
    """部分 stale 是个股停牌，**不是**数据未就绪：照常扫其余标的并落盘
    （run_daily_signal 的语义也是"全部落后才退出"）。"""
    monkeypatch.setattr(FakeService, "bars_per_symbol", HEALTHY_BARS)
    monkeypatch.setattr(FakeService, "last_bars",
                        {FAKE_ROWS[0][0]: PREV_DAY, FAKE_ROWS[1][0]: PREV_DAY})

    _run_scan(tmp_path, monkeypatch, [])

    assert "stale=2" in capsys.readouterr().out
    assert _csvs(tmp_path) == [f"{BASE_DAY}.csv"]


# ---------------------------------------------------------------------------
# v0.2.4 扫描范围记录与产物隔离（设计 §2）
#
# 已经发生过的事故：产物文件名只有日期，`--limit N` 试跑**静默覆盖**同一天的全量结果。
# 本地 5 份扫描 CSV 里 3 份就是这么没的（08-24 原本 86 条信号、08-27 原本全量），
# output/ 不在版本控制内，找不回来。而面板显示"某日无信号"时，用户根本分不出
# 那是全市场真没机会，还是一次 3 只票冒烟测试的残渣——本项目一路在防的
# "不报错但结论错"，这次赔的是数据。
#
# 两条防线一起测：路径隔离（试跑写 `_limit{N}` 文件）+ 伴生 meta（每份产物记住自己的范围）。
# ---------------------------------------------------------------------------


def _scan_csv(tmp_path, name: str) -> Path:
    return tmp_path / "output" / "scan" / name


def _load_meta(tmp_path, name: str):
    from quant.signal import scan_meta
    return scan_meta.load_meta(_scan_csv(tmp_path, name))


def test_a_trial_run_never_clobbers_the_full_scan_of_the_same_day(tmp_path, monkeypatch):
    """本次的核心回归：先跑一次全量，再跑 `--limit 1`，全量 CSV 必须**逐字节未变**。

    隔离靠路径、不靠约定：试跑落在 `<date>_limit1.csv`，两份结果各自成文件，
    文件系统层面就不可能互相覆盖。
    """
    monkeypatch.setattr(FakeService, "bars_per_symbol", HEALTHY_BARS)
    _run_scan(tmp_path, monkeypatch, [])
    full = _scan_csv(tmp_path, f"{BASE_DAY}.csv")
    before = full.read_bytes()
    assert before.count(b"\n") == len(FAKE_ROWS) + 1, "全量那趟没扫出该有的信号，这条测试没在测它"

    _run_scan(tmp_path, monkeypatch, ["--limit", "1"])

    assert full.read_bytes() == before, "试跑把同一天的全量扫描结果覆盖了"
    assert _csvs(tmp_path) == [f"{BASE_DAY}.csv", f"{BASE_DAY}_limit1.csv"], \
        "试跑没有落在自己的 _limit 路径上"
    trial = _scan_csv(tmp_path, f"{BASE_DAY}_limit1.csv")
    assert trial.read_bytes().count(b"\n") == 2, "试跑产物应只有 1 只标的的信号"


def test_the_two_products_carry_their_own_scope(tmp_path, monkeypatch):
    """两份 meta 各自正确：全量说"扫了 4 只（全部）"，试跑说"扫了 1 只（池子有 4 只）"。

    meta 必须**单独成文件**而不是塞进 CSV：信号数为 0 时 CSV 只有表头，
    没有任何地方能承载"我扫了 3010 只"这个事实——而那正是要区分的核心。
    """
    monkeypatch.setattr(FakeService, "bars_per_symbol", HEALTHY_BARS)
    _run_scan(tmp_path, monkeypatch, [])
    _run_scan(tmp_path, monkeypatch, ["--limit", "1"])

    full = _load_meta(tmp_path, f"{BASE_DAY}.csv")
    trial = _load_meta(tmp_path, f"{BASE_DAY}_limit1.csv")

    assert full.date == BASE_DAY and trial.date == BASE_DAY
    assert (full.scanned, full.pool_total, full.limit) == (len(FAKE_ROWS), len(FAKE_ROWS), None)
    assert full.is_full is True and full.signals == len(FAKE_ROWS)
    assert (trial.scanned, trial.pool_total, trial.limit) == (1, len(FAKE_ROWS), 1)
    assert trial.is_full is False and trial.signals == 1


def test_the_meta_records_the_skip_breakdown_and_failures(tmp_path, monkeypatch):
    """meta 要能替代终端汇总那一行：跳过明细、失败数、耗时、起始时刻。
    终端输出会滚走，这份文件是事后唯一的第一手证据。"""
    monkeypatch.setattr(FakeService, "bars_per_symbol", HEALTHY_BARS)
    monkeypatch.setattr(FakeService, "fail_symbols", (FAKE_ROWS[0][0],))
    monkeypatch.setattr(FakeService, "last_bars", {FAKE_ROWS[1][0]: PREV_DAY})

    _run_scan(tmp_path, monkeypatch, [])

    meta = _load_meta(tmp_path, f"{BASE_DAY}.csv")
    assert meta.failed == 1
    assert meta.skipped["stale"] == 1
    assert set(meta.skipped) == set(run_market_scan.SKIP_KEYS)
    assert meta.elapsed_s >= 0 and meta.started_at is not None


def test_no_meta_is_written_when_the_readiness_gates_reject_the_run(tmp_path, monkeypatch):
    """闸门不通过时 CSV 和 meta **都**不写。只写 meta 会更糟：
    面板会看见一个说"扫了 4 只"的范围记录，配着一份根本不存在的结果。"""
    monkeypatch.setattr(FakeService, "last_bar", PREV_DAY)

    with pytest.raises(SystemExit):
        _run_scan(tmp_path, monkeypatch, [])

    assert _csvs(tmp_path) == []
    assert list((tmp_path / "output").rglob("*.meta.json")) == []


def test_a_saturday_writes_neither_csv_nor_meta(tmp_path, monkeypatch):
    _seed_fresh_listing(tmp_path)

    with pytest.raises(SystemExit):
        _run_scan(tmp_path, monkeypatch, [], date_arg=str(SATURDAY))

    assert list((tmp_path / "output").rglob("*.meta.json")) == []


def test_the_saved_path_printed_for_the_panel_is_the_real_one(tmp_path, monkeypatch,
                                                              capsys):
    """`已保存: <path>` 是面板解析产物路径的唯一钩子（runner/progress.py），
    试跑时它必须指向 `_limit` 那份，否则控制台会去读全量结果并当成本次输出。

    而且**只能有一行** `已保存:`：meta 的路径若也用这个前缀打出来，
    面板会把那份 JSON 当成 CSV 去读，直接一句"产物读取失败"。
    """
    monkeypatch.setattr(FakeService, "bars_per_symbol", HEALTHY_BARS)
    _run_scan(tmp_path, monkeypatch, ["--limit", "2"])

    out = capsys.readouterr().out
    saved = [line for line in out.splitlines() if line.startswith("已保存: ")]
    assert len(saved) == 1, f"「已保存:」不止一行: {saved}"
    assert saved[0].endswith(f"{BASE_DAY}_limit2.csv"), saved[0]


# ================================================================ 不许用更差的一趟覆盖更好的（v0.5.0）

def _write_prior(scan_dir: Path, *, judged: int, signals: int = 150) -> tuple[Path, str]:
    """伪造"上一趟"的产物：CSV + meta。judged = scanned − failed。"""
    scan_dir.mkdir(parents=True, exist_ok=True)
    csv = scan_dir / f"{BASE_DAY}.csv"
    body = ("date,symbol,name,strategy,close,pct_chg,amount,amount_ratio_20d\n"
            f"{BASE_DAY},600519,贵州茅台,ma_cross,1500.0,1.2,3.0e8,1.8\n")
    csv.write_text(body, encoding="utf-8")
    scan_meta.save_meta(scan_meta.ScanMeta(
        date=BASE_DAY, scanned=len(FAKE_ROWS), pool_total=len(FAKE_ROWS), limit=None,
        signals=signals, skipped={}, failed=len(FAKE_ROWS) - judged,
        elapsed_s=900.0, started_at=datetime(2026, 8, 26, 18, 30)), csv)
    return csv, body


def test_a_less_complete_rerun_does_not_clobber_the_better_one(tmp_path, monkeypatch):
    """v0.2.4 的路径隔离只按 `--limit` 隔，按"走完了多少只判定"没隔。

    真实剧本：第一趟断网前扫完了大半（部分失败但成果可观）→ 徽标 tooltip 劝你
    「重跑一次即可补上」→ 第二趟网络更差、走完的更少但不为零 → **同名覆盖**，
    那份好的永久没了（output/ 不在版本控制里，也没有 .bak）。

    处理方式是**两份都留**：这一趟确实跑完了，它的结果有权存在，但不能顶掉更完整的。
    """
    scan_dir = tmp_path / "output" / "scan"
    # 上一趟：4 只全部走完判定。本趟：让 3 只取数失败 → judged=1，更差
    csv, good = _write_prior(scan_dir, judged=len(FAKE_ROWS))
    monkeypatch.setattr(FakeService, "fail_symbols", tuple(s for s, _ in FAKE_ROWS[:3]))

    _run_scan(tmp_path, monkeypatch, [])

    assert csv.read_text(encoding="utf-8") == good, "更完整的那份被覆盖了"
    side = [p.name for p in scan_dir.glob(f"{BASE_DAY}_judged*.csv")]
    assert side, f"本趟的结果没有另存下来：{sorted(p.name for p in scan_dir.iterdir())}"


def test_a_more_complete_rerun_overwrites_normally(tmp_path, monkeypatch):
    """反过来（这趟更完整）就该正常覆盖——那正是"重跑补上"要的效果。"""
    scan_dir = tmp_path / "output" / "scan"
    csv, good = _write_prior(scan_dir, judged=1)      # 上一趟只走完 1 只

    _run_scan(tmp_path, monkeypatch, [])              # 本趟 4 只全走完

    assert csv.read_text(encoding="utf-8") != good, "更完整的一趟没有覆盖更差的那份"
    assert list(scan_dir.glob(f"{BASE_DAY}_judged*.csv")) == [], "不该另存旁路文件"


def test_an_old_product_without_meta_is_not_mistaken_for_more_complete(tmp_path, monkeypatch):
    """v0.2.4 之前的老产物有 CSV 无 meta（load_meta 返回 None）。
    拿不到"上一趟走完多少只"就不许拦——那会让老产物永远挡着新结果。"""
    scan_dir = tmp_path / "output" / "scan"
    scan_dir.mkdir(parents=True)
    csv = scan_dir / f"{BASE_DAY}.csv"
    csv.write_text("date,symbol\n", encoding="utf-8")

    _run_scan(tmp_path, monkeypatch, [])

    assert list(scan_dir.glob(f"{BASE_DAY}_judged*.csv")) == [], "被老产物误拦了"
    assert csv.read_text(encoding="utf-8") != "date,symbol\n", "新结果没写进去"


# ================================================================ 提前判定「数据未就绪」（v0.5.0）

def test_the_readiness_gate_also_runs_at_the_first_progress_checkpoint():
    """整趟闸门在循环**之后**：17:30 前起全量扫描要跑完 3012 只才被告知
    「数据未就绪」——白等 15~25 分钟。所以在第一个进度检查点（前 100 只）再判一次。

    这里做**源码级**断言而不是端到端跑：测试夹具只有 4 只票，凑不到
    PROGRESS_EVERY=100 那个检查点，端到端跑不到这条分支。
    要紧的是它必须复用同一个判据函数（同一句原文、分母同样扣掉失败票），
    而不是另发明一个阈值——两套判据迟早给出不同答案。
    """
    src = (Path(__file__).resolve().parent.parent / "scripts"
           / "run_market_scan.py").read_text(encoding="utf-8")
    early = src[src.index("if i == PROGRESS_EVERY"):]
    assert "data_not_ready_reason(" in early[:400], "提前判定没复用同一个判据函数"
    assert "scanned=i - len(failures)" in early[:400], \
        "分母没扣掉失败票——那会让 1 只失败就永远判不出未就绪"
    assert "sys.exit" in early[:600], "判出未就绪却没退出"
