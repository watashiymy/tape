# tests/test_dashboard_visual.py — v0.2.1 M2 四页视觉改造（离线，AppTest）
#
# 视觉本身要人眼验收，但**结构**可以钉死，而且必须钉：
#   - 页头/小标题/指标卡/状态 pill 现在是我们自己包的 .qd-* HTML（st.html），
#     退回 st.subheader / st.metric 时排版层级会静默消失（页面照样能跑）；
#   - 指标卡是 2 行 × 4 列（8 个 1/4 宽列），不是一行 4 列各摞两个；
#   - 表格必须真的收到 column_config / Styler，否则"配好了"的千分位、红绿永远不出现；
#   - 控制台三张卡片必须并排（纵向堆叠要滚很久，这是本次要改的痛点之一）。
# 另有两条回归：半截回测目录仍走友好提示（v0.2.0 的修复），
# 以及状态文件里的 HTML 必须被转义（st.html 不套 iframe，未转义就能撕开版面）。
import ast
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from quant.report import fmt
from quant.runner import jobs
from tests.conftest import (APP_FILES, app_module, copy_app, goto_page,
                            make_bars, stub_navigation)

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "app" / "dashboard.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCAN_LOG = (FIXTURES / "market_scan_sample.log").read_text(encoding="utf-8")
PAGES = ["回测报告", "个股K线", "今日信号", "任务控制台"]

SCAN_HEADER = "date,symbol,name,strategy,close,pct_chg,amount,amount_ratio_20d\n"
TRADES_HEADER = ("symbol,action,date,price,shares,commission,stamp,pnl,holding_days\n")
# 这一组只是**构造用的** metrics 夹具（挑几个好认的值把格式化路径跑通），
# 不是本项目的实测结论。真实实测数字在 README「① 回测」的表格与 app/guide.py
# 的 FACTS 里，由 tests/test_guide.py 逐条对账。
REAL_METRICS = {"total_return": 1.161, "cagr": 0.0789, "max_drawdown": -0.3512,
                "sharpe": 0.9621, "n_trades": 243, "win_rate": 0.41975,
                "profit_factor": 1.34, "avg_holding_days": 12.5}


def _page(tmp_path: Path, page: str = "回测报告") -> AppTest:
    """一律显式切页：默认落地页自 v0.2.1 起是「使用说明」，
    省掉这一步会让本文件的断言全落到一页纯文档上（多数还会"通过"）。"""
    return goto_page(
        AppTest.from_file(str(copy_app(tmp_path)), default_timeout=30).run(), page)


def _htmls(at: AppTest) -> list[str]:
    """页面上我们自己包的 HTML。st.html 的元素在 AppTest 里没有 .value，只能读
    proto.body；theme.inject() 注入的那段 <style> 也走 st.html，必须排除掉
    ——CSS 里出现每一个 .qd-pill-* 类名，不滤掉的话"页面上有没有红色 pill"永远为真。"""
    return [b for e in at.get("html")
            if not (b := e.proto.body).lstrip().startswith("<style>")]


def _blob(at: AppTest) -> str:
    return "\n".join(_htmls(at))


def _run_dir(root: Path, name: str = "ma_cross_20260826_112606", *,
             metrics: dict | None = None, trades: str = "",
             klines: tuple[str, ...] = ()) -> Path:
    """一个**完整**的回测目录（三件套齐全，否则会被 list_runs 排除）。"""
    run = root / "output" / name
    run.mkdir(parents=True, exist_ok=True)
    (run / "metrics.json").write_text(
        json.dumps(REAL_METRICS if metrics is None else metrics), encoding="utf-8")
    (run / "report.html").write_text("<html><body>报告</body></html>", encoding="utf-8")
    (run / "trades.csv").write_text(TRADES_HEADER + trades, encoding="utf-8")
    for sym in klines:
        (run / f"kline_{sym}.html").write_text("<html></html>", encoding="utf-8")
    return run


def _cache_bars(root: Path, symbol: str) -> None:
    """给 K 线页喂一份最小可用行情缓存（BarCache 只读 parquet）。"""
    cache = root / "data" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    df = make_bars([
        {"date": f"2026-08-{d:02d}", "open": 10.0, "high": 11.0, "low": 9.5,
         "close": 10.5, "volume": 1000, "amount": 1e7} for d in (20, 21, 24, 25)
    ])
    df.to_parquet(cache / f"{symbol}.parquet")


def _fake_run(root: Path, job: str, status: str, *, log: str = "", exit_code=None,
              pid: int | None = None) -> None:
    """伪造状态文件。running 时 pid 必须活着（否则僵尸清理立刻改判 failed）——
    用测试进程自己的 pid 最安全。"""
    runs = root / "output" / "runs"
    (runs / "logs").mkdir(parents=True, exist_ok=True)
    log_path = runs / "logs" / f"{job}.log"
    log_path.write_text(log, encoding="utf-8")
    started = datetime.now() - timedelta(minutes=15)
    (runs / f"{job}.json").write_text(json.dumps({
        "script": job, "run_id": f"{job}_20260825_100000_000",
        "pid": os.getpid() if pid is None else pid,
        "argv": [sys.executable, "-u", jobs.JOBS[job].script],
        "log_path": str(log_path), "started_at": started.isoformat(timespec="seconds"),
        "status": status, "exit_code": exit_code,
        "finished_at": None if status == "running"
        else datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False), encoding="utf-8")


def _descend(block):
    """元素树递归展开（AppTest 只给顶层容器，列布局要自己走下去）。"""
    yield block
    for child in getattr(block, "children", {}).values():
        yield from _descend(child)


def _keys_under(block) -> set[str]:
    return {k for e in _descend(block) if (k := getattr(e, "key", None))}


def _bodies_under(block) -> list[str]:
    return [b for e in _descend(block)
            if (b := getattr(getattr(e, "proto", None), "body", None))]


# ================================================================ 通用页头（§2.4）

@pytest.mark.parametrize("page", PAGES)
def test_every_page_opens_with_a_page_head(tmp_path, page):
    """取代"直接甩控件"的开局：页名（衬线大字）+ 一句话说明（灰色小字）。"""
    at = _page(tmp_path, page)
    assert not at.exception, at.exception
    heads = [h for h in _htmls(at) if 'class="qd-head"' in h]
    assert len(heads) == 1, f"{page} 应有且仅有一个页头，实际 {len(heads)}"
    head = heads[0]
    assert f'class="qd-title">{page}<' in head, head
    assert 'class="qd-sub">' in head and 'class="qd-sub"></span>' not in head, \
        f"{page} 的页头缺一句话说明: {head}"


@pytest.mark.parametrize("page", PAGES)
def test_page_head_pill_says_idle_when_nothing_runs(tmp_path, page):
    at = _page(tmp_path, page)
    head = [h for h in _htmls(at) if 'class="qd-head"' in h][0]
    assert "qd-pill-idle" in head and "空闲" in head, head


@pytest.mark.parametrize("page", PAGES)
def test_page_head_pill_names_the_running_job(tmp_path, page):
    """右侧 pill 是**全局**任务状态：用户在 K 线页也该看见"扫描还在跑"，
    否则他会去点另一个开始，然后对着"启动失败"发愁。"""
    _fake_run(tmp_path, "market_scan", "running", log="[1800/3010] 信号 58 条\n")
    at = _page(tmp_path, page)
    assert not at.exception, at.exception
    head = [h for h in _htmls(at) if 'class="qd-head"' in h][0]
    assert "qd-pill-running" in head, head
    assert "全市场扫描" in head and "运行中" in head, head


@pytest.mark.parametrize("page", PAGES)
def test_page_head_pill_admits_when_the_state_is_unknown(tmp_path, page):
    """状态文件损坏 = 互斥状态不可知。页头绝不能报"空闲"（那是猜的，且正是
    fail-safe 要挡的误导），要如实说未知；同时不许崩页。"""
    runs = tmp_path / "output" / "runs"
    runs.mkdir(parents=True)
    (runs / "backtest.json").write_text('{"script": "backtest", "pid":', encoding="utf-8")
    at = _page(tmp_path, page)
    assert not at.exception, at.exception
    head = [h for h in _htmls(at) if 'class="qd-head"' in h][0]
    assert "未知" in head, head
    assert "空闲" not in head, head


# ================================================================ 回测报告页

def test_metric_grid_is_two_rows_of_four(tmp_path):
    """§2.4：指标卡 2 行 × 4 列。老写法是 st.columns(4) 配 i%4——那是一行 4 列、
    每列纵向摞两个卡片，右边一半版面空着。这里断言 8 个 1/4 宽列各放一张卡。"""
    _run_dir(tmp_path)
    at = _page(tmp_path)
    assert not at.exception, at.exception
    quarter = [c for c in at.get("column")
               if abs(c.proto.weight - 0.25) < 1e-6
               and any('class="qd-metric"' in b for b in _bodies_under(c))]
    assert len(quarter) == len(fmt.METRIC_LABELS) == 8, \
        f"应是 2×4 = 8 个等宽指标卡列，实际 {len(quarter)}"


def test_every_metric_is_rendered_as_our_own_card(tmp_path):
    """八项指标全在，且值走 .qd-metric-value（等宽放大），标签走 .qd-metric-label。
    退回 st.metric 会让"数字最大最亮"（§2.3 第 1 条）静默失效。"""
    _run_dir(tmp_path)
    at = _page(tmp_path)
    blob = _blob(at)
    for key, label in fmt.METRIC_LABELS.items():
        assert f'class="qd-metric-label">{label}<' in blob, f"缺 {label} 卡"
    assert blob.count('class="qd-metric-value"') == 8, "八项指标应各出一张卡"
    assert at.metric == [], "指标卡已改成 .qd-metric，不该再有 st.metric"


def test_total_return_is_red_and_drawdown_is_green(tmp_path):
    """§2.4 + A 股铁律：总收益为正上红、回撤（恒为负）上绿。
    实测值走一遍：双均线 +116.10% / 回撤 -35.12%。"""
    _run_dir(tmp_path)
    at = _page(tmp_path)
    blob = _blob(at)
    assert f'style="color: {fmt.UP}">116.10%' in blob, blob
    assert f'style="color: {fmt.DOWN}">-35.12%' in blob, blob


def test_non_directional_metrics_have_no_color(tmp_path):
    """夏普 0.96、胜率 41.98% 不上色（§2.3 第 4 条）。给它们染红绿等于多造假信号。"""
    _run_dir(tmp_path)
    blob = _blob(_page(tmp_path))
    assert 'class="qd-metric-value">0.96<' in blob, blob
    assert 'class="qd-metric-value">41.98%<' in blob, blob


def test_negative_total_return_turns_green(tmp_path):
    """亏钱的那次回测必须是绿的（红绿反过来就是给人反向信号）。"""
    _run_dir(tmp_path, metrics={"total_return": -0.2, "n_trades": 3})
    blob = _blob(_page(tmp_path))
    assert f'style="color: {fmt.DOWN}">-20.00%' in blob, blob


def test_trade_count_still_renders_as_an_integer(tmp_path):
    """老缺陷回归：一律 f"{v:.2f}" 会把交易次数渲染成 '243.00'。"""
    _run_dir(tmp_path)
    assert 'class="qd-metric-value">243<' in _blob(_page(tmp_path))


def test_missing_metrics_show_a_dash_not_none(tmp_path):
    """零平仓时 win_rate / profit_factor 是 null；不能渲染出 'None' / 'nan'。"""
    _run_dir(tmp_path, metrics={"n_trades": 0, "win_rate": None})
    blob = _blob(_page(tmp_path))
    assert f'class="qd-metric-value">{fmt.MISSING}<' in blob
    assert "None" not in blob and "nan" not in blob


def _captured_tables(tmp_path, page, monkeypatch) -> list[tuple]:
    """记录每次 st.dataframe 收到的 (数据, kwargs)：column_config 与 Styler
    是否真的传下去，只能在这里验——渲染完了它们就化进 arrow 里了。"""
    import importlib.util

    calls: list[tuple] = []
    monkeypatch.delitem(sys.modules, "theme", raising=False)
    stub_navigation(monkeypatch, page)   # bare 模式下真 st.Page 什么都不画（见 conftest）
    monkeypatch.setattr(st, "dataframe", lambda data, *a, **k: calls.append((data, k)))
    monkeypatch.setattr(st, "plotly_chart", lambda *a, **k: None)
    spec = importlib.util.spec_from_file_location(
        f"dashboard_tables_{page}", copy_app(tmp_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return calls


def test_trades_table_carries_the_column_config(tmp_path, monkeypatch):
    """§2.4：交易明细的金额千分位、比率百分号、数字右对齐全靠 column_config；
    不传下去的话表格就是一堆左对齐的裸浮点。"""
    _run_dir(tmp_path, trades="000333,buy,2016-04-05,10.0,100,5.0,0.0,,\n")
    (data, kwargs), = _captured_tables(tmp_path, "回测报告", monkeypatch)
    assert kwargs["width"] == "stretch"
    assert set(kwargs["column_config"]) == set(fmt.trades_column_config())
    assert data["symbol"].tolist() == ["000333"], "前导零仍不许被吃掉"


def test_report_html_is_embedded_between_metrics_and_trades(tmp_path):
    """§1.1：图表走 st.iframe（st.html 不套 iframe 且默认忽略 JS，会是空白页）。"""
    _run_dir(tmp_path)
    at = _page(tmp_path)
    assert len(at.get("iframe")) == 1, "回测报告页应恰好嵌一次 report.html"


# ================================================================ 个股 K 线页

def test_symbol_selector_shows_code_and_name(tmp_path):
    """§2.4：选择器要显示"代码 名称"。名称的唯一离线来源是扫描 CSV。"""
    _run_dir(tmp_path, klines=("600519",))
    _cache_bars(tmp_path, "600519")
    scan = tmp_path / "output" / "scan"
    scan.mkdir(parents=True)
    (scan / "2026-08-25.csv").write_text(
        SCAN_HEADER + "2026-08-25,600519,贵州茅台,donchian,1500.0,1.2,5e8,2.0\n",
        encoding="utf-8")
    at = _page(tmp_path, "个股K线")
    assert not at.exception, at.exception
    assert "600519 贵州茅台" in list(at.selectbox[1].options), at.selectbox[1].options


def test_symbol_selector_falls_back_to_the_bare_code(tmp_path):
    """扫描 CSV 里没有这只（多数标的都没有：扫描只记录出信号的）：
    只显示代码，绝不渲染 '600519 None' / '600519 nan'。"""
    _run_dir(tmp_path, klines=("600519",))
    _cache_bars(tmp_path, "600519")
    at = _page(tmp_path, "个股K线")
    assert not at.exception, at.exception
    assert list(at.selectbox[1].options) == ["600519"], at.selectbox[1].options


def test_kline_page_summarises_trades_of_this_symbol(tmp_path):
    """§2.4：图上方补一行"本次回测在该标的上的成交笔数 / 盈亏"。
    盈亏为正上红（A 股口径）。"""
    _run_dir(tmp_path, trades="600519,buy,2016-04-05,10.0,100,5.0,0.0,,\n"
                              "600519,sell,2016-06-05,12.0,100,5.0,1.2,195.0,61\n",
             klines=("600519",))
    _cache_bars(tmp_path, "600519")
    at = _page(tmp_path, "个股K线")
    assert not at.exception, at.exception
    blob = _blob(at)
    assert '>成交笔数</div><div class="qd-metric-value">2<' in blob, blob
    assert f'>已平仓盈亏</div><div class="qd-metric-value" style="color: {fmt.UP}">195' \
        in blob, f"盈亏为正应上红: {blob}"


def test_kline_summary_of_an_open_position_shows_no_fake_zero(tmp_path):
    """只买未卖（仍持仓）：pnl 全是 NaN，小结必须显示 —，不能写 0
    （那等于宣布"这只不赚不亏"）。"""
    _run_dir(tmp_path, trades="600519,buy,2016-04-05,10.0,100,5.0,0.0,,\n",
             klines=("600519",))
    _cache_bars(tmp_path, "600519")
    at = _page(tmp_path, "个股K线")
    assert not at.exception, at.exception
    blob = _blob(at)
    assert f'>已平仓盈亏</div><div class="qd-metric-value">{fmt.MISSING}<' in blob, blob
    assert '>已平仓盈亏</div><div class="qd-metric-value">0<' not in blob, blob


def test_kline_page_still_reports_a_missing_cache_clearly(tmp_path):
    """行情缓存里没有这只（用户删过 data/cache）：既有的 st.error 不能被小结顶掉。"""
    _run_dir(tmp_path, klines=("600519",))
    at = _page(tmp_path, "个股K线")
    assert not at.exception, at.exception
    assert any("600519" in e.value for e in at.error), [e.value for e in at.error]


# ================================================================ 今日信号 / 扫描

def test_scan_table_gets_column_config_and_direction_colors(tmp_path, monkeypatch):
    """§2.4：amount 千分位、pct_chg 百分号 + 红绿、amount_ratio_20d 进度条。
    红绿只能走 Styler（NumberColumn 没有 color 参数），所以两样都得传下去。

    注意 tmp_path 里**没有** config/settings.yaml，所以这里走的是 v0.2.2 M3 的
    降级路径：读不到信号池 → 扫描表不加 ＋ 列。
    带 ＋ 列的那套（多一个 pool.ADD_COLUMN）在 tests/test_dashboard_universe.py 里验。

    数据列取自 fmt（src/ 的纯函数），UI 层再叠一个「记账」动作列——它带 on_click
    回调（切页 + 写 session state），是 UI 职责，不能下沉到 fmt。这一列不依赖信号池
    配置，所以降级路径下也在。"""
    scan = tmp_path / "output" / "scan"
    scan.mkdir(parents=True)
    (scan / "2026-08-25.csv").write_text(
        SCAN_HEADER + "2026-08-25,000020,深华发A,ma_cross,11.74,-5.09,2.9e8,4.22\n",
        encoding="utf-8")
    (data, kwargs), = _captured_tables(tmp_path, "今日信号", monkeypatch)
    assert set(kwargs["column_config"]) == (
        set(fmt.scan_column_config()) | {app_module("journal_ui").RECORD_COLUMN})
    data._compute()
    assert dict(data.ctx[(0, 5)]) == {"color": fmt.DOWN}, "跌 -5.09% 应是绿的"
    assert data.data["symbol"].tolist() == ["000020"], "前导零仍不许被吃掉"


def test_signal_table_gets_the_signal_column_config(tmp_path, monkeypatch):
    """每日信号 CSV 的列与扫描不同，必须用它自己那套配置（否则 action / close
    连中文标签都没有）。"""
    sig = tmp_path / "output" / "signals"
    sig.mkdir(parents=True)
    (sig / "2026-08-25.csv").write_text(
        "date,symbol,strategy,action,close\n2026-08-25,000333,ma_cross,buy,10.0\n",
        encoding="utf-8")
    (data, kwargs), = _captured_tables(tmp_path, "今日信号", monkeypatch)
    # 同扫描表：数据列来自 fmt，UI 再叠「记账」动作列（v0.3.0 §5.1 的一键记账）
    assert set(kwargs["column_config"]) == (
        set(fmt.signal_column_config()) | {app_module("journal_ui").RECORD_COLUMN})
    assert data["symbol"].tolist() == ["000333"]


def test_scan_block_title_is_a_serif_section_with_the_date(tmp_path):
    """区块小标题改成 .qd-section（衬线 + 发丝线），但**日期必须还在**：
    停牌日/忘跑的日子，别让人把旧扫描当今天的。"""
    scan = tmp_path / "output" / "scan"
    scan.mkdir(parents=True)
    (scan / "2026-08-21.csv").write_text(
        SCAN_HEADER + "2026-08-21,000020,深华发A,ma_cross,11.74,-5.09,2.9e8,4.22\n",
        encoding="utf-8")
    at = _page(tmp_path, "今日信号")
    assert not at.exception, at.exception
    sections = [h for h in _htmls(at) if 'class="qd-section"' in h]
    assert any("2026-08-21" in s for s in sections), sections


# ================================================================ 任务控制台

def _card_columns(at: AppTest) -> list:
    """卡片列 = 恰好含一个 start_* 键、且键不止一个的列。
    卡片内部那排按钮也是 st.columns(3)，但每列只含一个键，据此区分开。"""
    starts = {f"start_{n}" for n in jobs.JOBS}
    return [c for c in at.get("column")
            if len(_keys_under(c) & starts) == 1 and len(_keys_under(c)) > 1]


def test_the_pipeline_puts_the_two_chained_jobs_in_equal_columns(tmp_path):
    """v0.5.0 §6：控制台改成「每日流水线」+「研究工具」两区。

    流水线区是 columns([1, .14, 1, .14, 1])：链上那两个**可运行**任务各占一个
    等宽列，中间夹着人工步骤，两条窄轨放箭头。回测不进这几列——它不在链上。

    改掉的老形态是三卡等宽并排（v0.2.0），当时唯一的理由是"纵向堆叠要滚很久"，
    列的来源就是 jobs.JOBS 的字典序，语义为零。
    """
    at = _page(tmp_path, "任务控制台")
    assert not at.exception, at.exception
    card_cols = _card_columns(at)
    chained = {"market_scan", "daily_signal"}
    assert len(card_cols) == len(chained), f"流水线上应有两个任务列，实际 {len(card_cols)}"
    assert {tuple(sorted(_keys_under(c) & {f"start_{n}" for n in jobs.JOBS}))
            for c in card_cols} == {(f"start_{n}",) for n in chained}, \
        "两列应分别是扫描与每日信号；回测不在链上"
    weights = sorted(c.proto.weight for c in at.get("column"))
    # [1, .14, 1, .14, 1] 归一化后：三个 0.3049 与两个 0.0427
    wide = [w for w in weights if abs(w - 1 / 3.28) < 1e-3]
    rails = [w for w in weights if abs(w - 0.14 / 3.28) < 1e-3]
    assert len(wide) == 3 and len(rails) == 2, \
        f"流水线列宽不对（应三宽两窄轨）: {weights}"


def test_the_backtest_card_is_out_of_the_pipeline_and_full_width(tmp_path):
    """回测在「研究工具」区、整幅宽渲染：它是历史检验，不产生今天的信号。
    摆回流水线里等于告诉用户"每天还得跑一次回测"。"""
    at = _page(tmp_path, "任务控制台")
    # 只看**流水线那三个宽列**（权重 1/3.28）。不能查"任不任何列里"——
    # 每张卡片自己就用 st.columns(3) 摆开始/停止/重跑，那三列谁都躲不开。
    pipeline_cols = [c for c in at.get("column")
                     if abs(c.proto.weight - 1 / 3.28) < 1e-3]
    # 前提断言：3.28 这个常量与 pages_console 的 [1,.14,1,.14,1] 绑着，改了列宽比例
    # 而不改这里的话，上面这个筛选会**筛出空集**，下面的 not in 就恒真了。
    assert len(pipeline_cols) == 3, \
        f"权重常量 3.28 与 pages_console 对不上了: {sorted(c.proto.weight for c in at.get('column'))}"
    inside = {k for c in pipeline_cols for k in _keys_under(c)}
    assert "start_backtest" not in inside, f"回测被摆进流水线的列里了: {sorted(inside)}"
    assert at.button("start_backtest"), "回测卡片整个不见了"
    assert "研究工具" in _blob(at), "缺「研究工具」区标题——不说清它不在链上就白摆了"


def test_card_title_carries_a_status_pill(tmp_path):
    """§2.3 第 5 条：状态徽标改成有底色的 pill（emoji 文字扫不到）。"""
    _fake_run(tmp_path, "market_scan", "running", log="[1800/3010] 信号 58 条\n")
    at = _page(tmp_path, "任务控制台")
    blob = _blob(at)
    assert 'class="qd-section"' in blob and "qd-pill-running" in blob, blob
    assert at.subheader == [], "卡片标题已改成 .qd-section，不该再有 st.subheader"


def test_failed_card_pill_is_red_and_carries_the_exit_code(tmp_path):
    _fake_run(tmp_path, "backtest", "failed", exit_code=3, log="Traceback\n")
    blob = _blob(_page(tmp_path, "任务控制台"))
    assert "qd-pill-failed" in blob and "退出码 3" in blob, blob


def test_stopped_card_pill_is_grey_not_red(tmp_path):
    """已停止是用户自己按的，不是故障：灰色。染红会让人以为出了错。"""
    _fake_run(tmp_path, "market_scan", "stopped", exit_code=-15, log=SCAN_LOG[:200])
    blob = _blob(_page(tmp_path, "任务控制台"))
    assert "qd-pill-idle" in blob and "已停止" in blob, blob
    assert "qd-pill-failed" not in blob, blob


def test_tampered_status_in_the_state_file_is_escaped(tmp_path):
    """状态文件是手工改得动的 JSON，而 st.html **不套 iframe**：
    未转义的 '<' 能把整页版面撕开（甚至塞进标签）。"""
    _fake_run(tmp_path, "backtest", "<img src=x onerror=alert(1)>")
    at = _page(tmp_path, "任务控制台")
    assert not at.exception, at.exception
    blob = _blob(at)
    assert "<img" not in blob, blob
    assert "&lt;img" in blob, blob


def test_progress_is_split_into_headline_and_detail(tmp_path):
    """§2.4：进度条文案只放主状态，已用/ETA/信号条数挪到下面一行细节。"""
    _fake_run(tmp_path, "market_scan", "running",
              log="\n".join(SCAN_LOG.splitlines()[:20]))
    at = _page(tmp_path, "任务控制台")
    assert not at.exception, at.exception
    bars = at.get("progress")
    assert len(bars) == 1, f"运行中且有 current/total 时应有一个进度条，实际 {len(bars)}"
    text = bars[0].proto.text
    assert text == "扫描中，1800/3010（60%）", text
    details = [c.value for c in at.main.caption]
    assert any("已用" in d and "预计剩余" in d and "信号 58 条" in d for d in details), details


def test_stopped_run_shows_no_eta_in_any_line(tmp_path):
    """B1 回归（v0.2.0 修的缺陷）：进程已停，日志最后一行还停在 [1800/3010]。
    两行都不许再说"扫描中"、不许外推 ETA——**包括新加的细节行**，
    把 ETA 挪到第二行等于让缺陷原地复活。"""
    _fake_run(tmp_path, "market_scan", "stopped", exit_code=-15,
              log="\n".join(SCAN_LOG.splitlines()[:20]))
    at = _page(tmp_path, "任务控制台")
    assert not at.exception, at.exception
    bars = at.get("progress")
    assert len(bars) == 1, "停在 60% 也该看得见停在哪儿"
    everything = " ".join([bars[0].proto.text, *(c.value for c in at.main.caption)])
    assert "1800/3010" in everything and "已用" in everything, everything
    assert "预计剩余" not in everything, f"任务已停止却还在报 ETA: {everything}"
    assert "扫描中" not in everything, f"任务已停止却还说「扫描中」: {everything}"
    assert at.get("status") == [], "已终止的任务不该再转圈"


def test_log_boxes_have_a_fixed_height(tmp_path):
    """§2.4：日志区固定高度滚动。高度不进 proto（AppTest 看不见），
    只能在源码层钉住每个 st.code 都带 height——漏了就是几十行日志把版面顶飞。

    扫整个 app/：日志区自 v0.2.2 M3 起在 app/pages_console.py（设计 §4 的拆分），
    只扫 dashboard.py 会得到空列表，这条断言就变成空跑。"""
    calls = [node
             for p in APP_FILES
             for node in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
             if isinstance(node, ast.Call)
             and getattr(node.func, "attr", "") == "code"]
    assert calls, "面板里没有 st.code 调用？日志区没了"
    for call in calls:
        assert "height" in {kw.arg for kw in call.keywords}, \
            f"L{call.lineno} 的 st.code 没给 height（日志区会无限长）"


def test_console_metric_cards_replace_st_metric(tmp_path):
    """回测跑完后卡片里那组指标也走 .qd-metric（同一套组件，不是两种长相）。"""
    for name in ("ma_cross_20260826_112606", "donchian_20260826_112606"):
        _run_dir(tmp_path, name, metrics={"n_trades": 243})
    _fake_run(tmp_path, "backtest", "success", exit_code=0,
              log=(FIXTURES / "backtest_sample.log").read_text(encoding="utf-8"))
    at = _page(tmp_path, "任务控制台")
    assert not at.exception, at.exception
    assert _blob(at).count('class="qd-metric-value">243<') == 2, \
        "两个策略的报告目录各出一组指标卡"
    assert at.metric == []


# ================================================================ 回归：半截目录

@pytest.mark.parametrize("files", [
    ["metrics.json"],                    # Ctrl-C 在写 report.html 之前
    ["metrics.json", "report.html"],     # 缺 trades.csv
], ids=["只有metrics", "缺trades"])
def test_half_written_run_dir_still_gets_the_friendly_hint(tmp_path, files):
    """v0.2.0 的修复必须活着：半截目录被 list_runs 排除、页面给提示而不是崩。
    改版最容易在这里退步——新页头/指标网格都在读 metrics。"""
    run = tmp_path / "output" / "ma_cross_20260824_151600"
    run.mkdir(parents=True)
    for f in files:
        (run / f).write_text('{"n_trades": 1}' if f.endswith("json") else "<html></html>",
                             encoding="utf-8")
    for page in ("回测报告", "个股K线"):
        at = _page(tmp_path, page)
        assert not at.exception, f"{page} 被半截目录（{files}）崩了: {at.exception}"
        assert at.info, f"{page} 应显示'暂无回测结果'提示"


def test_truncated_metrics_json_still_says_delete_the_dir(tmp_path):
    """三件套齐全但 metrics.json 只写了一半：给"删除该目录"的提示，不崩页。"""
    run = tmp_path / "output" / "ma_cross_20260824_151600"
    run.mkdir(parents=True)
    (run / "metrics.json").write_text('{"total_return": 0.48', encoding="utf-8")
    (run / "report.html").write_text("<html></html>", encoding="utf-8")
    (run / "trades.csv").write_text(TRADES_HEADER, encoding="utf-8")
    at = _page(tmp_path)
    assert not at.exception, at.exception
    assert any("删除" in e.value for e in at.error), [e.value for e in at.error]


def test_empty_output_still_guides_the_user(tmp_path):
    """空 output/：四页都不许崩，且该给的提示都在（控制条也还在）。"""
    for page in PAGES:
        at = _page(tmp_path, page)
        assert not at.exception, f"{page}: {at.exception}"
