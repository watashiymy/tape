# tests/test_dashboard_visual.py — v0.2.1 M2 四页视觉改造（离线，AppTest）
#
# 视觉本身要人眼验收，但**结构**可以钉死，而且必须钉：
#   - 页头/小标题/指标卡/状态 pill 现在是我们自己包的 .qd-* HTML（st.html），
#     退回 st.subheader / st.metric 时排版层级会静默消失（页面照样能跑）；
#   - 指标卡是 2 行 × 4 列（8 个 1/4 宽列），不是一行 4 列各摞两个；
#   - 表格必须真的收到 column_config / Styler，否则"配好了"的千分位、红绿永远不出现；
#   - 控制台的任务卡片整幅宽、自上而下（2026-11-16 改版；v0.2.0～v0.5.0 是并排的列——
#     结果表被挤掉最要紧的一列、两栏高度差出几屏，见 app/pages_console.py 的模块说明）。
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
from streamlit.proto.Block_pb2 import Block as BlockProto
from streamlit.testing.v1 import AppTest

from quant.report import fmt
from quant.runner import jobs
from tests.conftest import (APP_FILES, action_button_keys, app_module, copy_app, goto_page,
                            make_bars, sits_on_the_right, stub_navigation)

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "app" / "dashboard.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCAN_LOG = (FIXTURES / "market_scan_sample.log").read_text(encoding="utf-8")
PAGES = ["回测报告", "个股K线", "信号", "任务控制台"]
#: 控制台上真的有卡片的任务名单——market_breadth 的控件搬到了「市场概览」页
#: （app/pages_console.py 的 _ELSEWHERE），控制台的列结构断言不该数它。
CONSOLE_JOBS = ("market_scan", "daily_signal", "backtest")

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
    """取代"直接甩控件"的开局：页名（衬线大字）+ 一个帮助圆点。
    2026-11-16 起那句"这页给你看什么"不再常驻在页名旁（用户：这类解释性文字读起来是有成本的）
    ——它在圆点的提示框里，页头的明面上只有页名与状态。"""
    at = _page(tmp_path, page)
    assert not at.exception, at.exception
    heads = [h for h in _htmls(at) if 'class="qd-head"' in h]
    assert len(heads) == 1, f"{page} 应有且仅有一个页头，实际 {len(heads)}"
    head = heads[0]
    assert f'class="qd-title">{page}<span class="qd-help-dot' in head, head
    assert "qd-sub" not in head, f"{page} 的页名旁又有常驻的说明小字了: {head}"
    tip = head.split('class="qd-help-tip">', 1)[1]
    assert len(tip.split("</span>", 1)[0]) > 10, f"{page} 页名后面的圆点里没有话: {head}"
    assert "**" not in head and "\n" not in head, f"{page} 的页头说明没渲染干净: {head}"


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
    tables = _captured_tables(tmp_path, "回测报告", monkeypatch)
    # v0.5.0 起页面第一张表是「全部 N 次回测一览」（它 column_config 为空、无 symbol 列），
    # 所以按内容认成交明细那张，而不是按下标取第一张。
    data, kwargs = next((d, k) for d, k in tables if "symbol" in d.columns)
    assert kwargs["width"] == "stretch"
    assert set(kwargs["column_config"]) == set(fmt.trades_column_config())
    assert data["symbol"].tolist() == ["000333"], "前导零仍不许被吃掉"


def test_report_html_is_embedded_between_metrics_and_trades(tmp_path):
    """§1.1：图表走 st.iframe（st.html 不套 iframe 且默认忽略 JS，会是空白页）。"""
    _run_dir(tmp_path)
    at = _page(tmp_path)
    assert len(at.get("iframe")) == 1, "回测报告页应恰好嵌一次 report.html"


# ================================================================ 信号表的方向列（2026-09-05）

def test_signal_table_shows_direction_in_chinese_not_buy_sell(tmp_path):
    """信号 CSV 里存的是 BUY / SELL（内部值，命令行与文件认它），页面上必须显示 买入 / 卖出。"""
    sig = tmp_path / "output" / "signals"
    sig.mkdir(parents=True)
    (sig / "2026-08-25.csv").write_text(
        "date,symbol,strategy,action,close\n"
        "2026-08-25,000333,ma_cross,BUY,10.0\n2026-08-25,600519,donchian,SELL,1500.0\n",
        encoding="utf-8")
    at = _page(tmp_path, "信号")
    assert not at.exception, at.exception
    shown = at.get("dataframe")[0].value
    assert shown["action"].tolist() == ["买入", "卖出"], shown["action"].tolist()
    assert shown["strategy"].tolist() == ["双均线交叉", "唐奇安通道突破"]
    assert (sig / "2026-08-25.csv").read_text(encoding="utf-8").count("BUY") == 1, "文件本身不动"


# ================================================================ 回测总览表的列类型（2026-09-05）

def test_runs_overview_is_arrow_compatible_when_only_some_runs_have_a_snapshot(tmp_path):
    """「标的数」列里既有整数（有配置快照的回测）又有 —（老回测没有快照）时，Arrow 转不过去：
    Streamlit 自动降级并在控制台打一整段 traceback（真机出现过）。整张表一律字符串。"""
    import json

    import pyarrow as pa

    new = _run_dir(tmp_path, name="ma_cross_20260905_100000")
    (new / "config_snapshot.json").write_text(
        json.dumps({"universe": ["600519", "000333"], "overlays": {}}), encoding="utf-8")
    _run_dir(tmp_path, name="donchian_20260826_112606")          # 老回测：没有快照
    at = _page(tmp_path, "回测报告")
    assert not at.exception, at.exception
    overview = at.get("dataframe")[0].value                       # 折叠区里那张总览表排第一
    assert set(overview["标的数"]) == {"2", fmt.MISSING}, overview["标的数"].tolist()
    assert all(isinstance(v, str) for v in overview["标的数"])
    pa.Table.from_pandas(overview)                                # 不该抛 ArrowInvalid


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


def test_kline_page_offers_day_week_month_year(tmp_path):
    """周期切换（2026-09-05）：日/周/月/年四档，切到周K照常出图。"""
    import json

    _run_dir(tmp_path, klines=("600519",))
    _cache_bars(tmp_path, "600519")
    at = _page(tmp_path, "个股K线")
    assert not at.exception, at.exception
    radio = at.radio(key="kline_freq")
    # AppTest 给的 options 是 format_func 之后的显示名；value / set_value 走内部键
    assert list(radio.options) == ["日K", "周K", "月K", "年K"]
    assert radio.value == "D"
    span = at.radio(key="kline_span")
    assert list(span.options) == ["120 根", "250 根", "500 根", "全部"]
    assert span.value == "120", "默认只画最近 120 根：一次画全部就是「太细太密」"
    assert len(_kline_components(at)) == 1
    at = span.set_value("all").run()
    assert not at.exception, at.exception
    at = radio.set_value("W").run()
    assert not at.exception, at.exception
    spec = _kline_args(at)["spec"]
    candles = [tr for tr in spec["data"] if tr["type"] == "candlestick"][0]
    # 夹具是 08-20/21（周四五）与 08-24/25（周一二）：周K正好两根
    assert len(candles["x"]) == 2, candles["x"]


def _kline_components(at: AppTest) -> list:
    """K 线走项目自己的 iframe 组件（app/kline_view.py），AppTest 里是 component_instance。"""
    return [e for e in at.get("component_instance")
            if e.proto.component_name.endswith(".tape_kline")]


def _kline_args(at: AppTest) -> dict:
    import json
    return json.loads(_kline_components(at)[0].proto.json_args)


def test_kline_page_enables_scroll_zoom_and_explains_the_gestures(tmp_path):
    """滚轮/双指缩放是**前端配置**，图对象自己开不了——必须真的传到前端（组件 args 里的
    config）。手势与 ▲▼ 的位置在页名后面的帮助圆点里说清——2026-11-16 之前是图下面常驻的
    三行灰字，那三行不许再回到图下面。"""
    _run_dir(tmp_path, klines=("600519",))
    _cache_bars(tmp_path, "600519")
    at = _page(tmp_path, "个股K线")
    assert not at.exception, at.exception
    args = _kline_args(at)
    assert args["config"].get("scrollZoom") is True, args["config"]
    assert args["config"].get("responsive") is True
    head = [h for h in _htmls(at) if 'class="qd-head"' in h][0]
    tip = head.split('class="qd-help-tip">', 1)[1]
    assert "双指" in tip and "双击" in tip and "▲" in tip, tip
    captions = " ".join(c.value for c in at.caption)
    assert "双指" not in captions and "▲" not in captions, captions


def test_kline_page_does_not_use_st_plotly_chart(tmp_path):
    """2026-09-05：st.plotly_chart 在缩放中途反复重画整图（Streamlit 的 onUpdate 把每个
    plotly_relayouting 中间态写回 state），触控板双指缩放会抽动——真机实测 20 次事件里
    横轴范围回弹三轮。K 线必须走自己的 iframe 组件；这条钉住别改回去。"""
    _run_dir(tmp_path, klines=("600519",))
    _cache_bars(tmp_path, "600519")
    at = _page(tmp_path, "个股K线")
    assert not at.exception, at.exception
    assert at.get("plotly_chart") == [], "K 线又走回 st.plotly_chart 了"
    args = _kline_args(at)
    assert args["plotly_js"].startswith("../") and args["plotly_js"].endswith("/plotly.min.js")
    assert args["height"] == 550 and args["bg"]
    assert [tr["type"] for tr in args["spec"]["data"]][0] == "candlestick"


def test_kline_page_still_reports_a_missing_cache_clearly(tmp_path):
    """行情缓存里没有这只（用户删过 data/cache）：既有的 st.error 不能被小结顶掉。"""
    _run_dir(tmp_path, klines=("600519",))
    at = _page(tmp_path, "个股K线")
    assert not at.exception, at.exception
    assert any("600519" in e.value for e in at.error), [e.value for e in at.error]


# ================================================================ 信号 / 扫描

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
    (data, kwargs), = _captured_tables(tmp_path, "信号", monkeypatch)
    assert set(kwargs["column_config"]) == (
        set(fmt.scan_column_config()) | {app_module("journal_ui").RECORD_COLUMN})
    data._compute()
    assert dict(data.ctx[(0, 5)]) == {"color": fmt.DOWN}, "跌 -5.09% 应是绿的"
    assert data.data["symbol"].tolist() == ["000020"], "前导零仍不许被吃掉"


def test_signal_table_gets_the_signal_column_config(tmp_path, monkeypatch):
    """信号跟踪 CSV 的列与扫描不同，必须用它自己那套配置（否则 action / close
    连中文标签都没有）。"""
    sig = tmp_path / "output" / "signals"
    sig.mkdir(parents=True)
    (sig / "2026-08-25.csv").write_text(
        "date,symbol,strategy,action,close\n2026-08-25,000333,ma_cross,buy,10.0\n",
        encoding="utf-8")
    (data, kwargs), = _captured_tables(tmp_path, "信号", monkeypatch)
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
    at = _page(tmp_path, "信号")
    assert not at.exception, at.exception
    sections = [h for h in _htmls(at) if 'class="qd-section"' in h]
    assert any("2026-08-21" in s for s in sections), sections


# ================================================================ 任务控制台

def test_no_column_holds_a_whole_card(tmp_path):
    """2026-11-16 改版：每张任务卡片**整幅宽**，不再塞进并排的列里。

    v0.5.0 是 columns([1, .14, 1]) 两栏并排（更早的 v0.2.0 是三卡等宽）。真机上的毛病：
    扫描结果表挤在半幅宽里，说明页说"最该看"的「放量倍数」一列被挤出了可视区；
    左栏摊着日志与结果、右栏只有几行，高度差出几屏，「研究工具」被推到第三屏。

    怎么认"整幅宽"：卡片的主按钮在标题行、重跑在下方的「更多选项」里，是卡片的头和尾。
    任何一个**列**同时装着这两个，就说明整张卡片被塞进了列里。
    """
    at = _page(tmp_path, "任务控制台")
    assert not at.exception, at.exception
    for name in CONSOLE_JOBS:          # 前提：卡片确实都在，否则下面的"不在列里"恒真
        assert at.button(f"start_{name}") and at.button(f"rerun_{name}"), name
    for col in at.get("column"):
        keys = _keys_under(col)
        for name in CONSOLE_JOBS:
            assert not {f"start_{name}", f"rerun_{name}"} <= keys, \
                f"「{jobs.JOBS[name].label}」整张卡片被塞进了一个列里: {sorted(keys)}"


def test_the_backtest_card_sits_in_its_own_zone_below_the_pipeline(tmp_path):
    """回测在「研究工具」区：它是历史检验，不产生今天的信号。
    摆回流水线里等于告诉用户"每天还得跑一次回测"。"""
    at = _page(tmp_path, "任务控制台")
    order = [k if (k := getattr(e, "key", None)) else
             (getattr(getattr(e, "proto", None), "body", None) or "")
             for e in _descend(at.main)]

    def first(needle: str) -> int:
        hits = [i for i, item in enumerate(order) if needle in item]
        assert hits, f"页面上找不到 {needle!r}"
        return hits[0]

    assert first("start_market_scan") < first("start_daily_signal") < first("研究工具") \
        < first("start_backtest"), "回测应在「研究工具」区标题之下、两张流水线卡片之后"


def test_card_title_carries_a_status_pill(tmp_path):
    """§2.3 第 5 条：状态徽标改成有底色的 pill（emoji 文字扫不到）。"""
    _fake_run(tmp_path, "market_scan", "running", log="[1800/3010] 信号 58 条\n")
    at = _page(tmp_path, "任务控制台")
    titles = [h for h in _htmls(at) if 'class="qd-task-title"' in h]
    assert len(titles) == len(CONSOLE_JOBS), titles
    scan = next(t for t in titles if f'>{jobs.JOBS["market_scan"].label}<' in t)
    assert "qd-pill-running" in scan, scan
    assert at.subheader == [], "卡片标题是我们自己包的 .qd-task-title，不该再有 st.subheader"


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


def _fixed_height_containers(tree: ast.AST) -> list[ast.With]:
    """源码里所有 `with st.container(height=<整数/常量名>, ...)` 语句。"""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            call = item.context_expr
            if (isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "container"
                    and any(kw.arg == "height" and not (isinstance(kw.value, ast.Constant)
                                                         and isinstance(kw.value.value, str))
                            for kw in call.keywords)):
                out.append(node)
    return out


def test_log_boxes_have_a_fixed_height(tmp_path):
    """§2.4：日志区固定高度滚动。高度不进 proto（AppTest 看不见），
    只能在源码层钉住——漏了就是几十行日志把版面顶飞。

    两种合法写法：
    - `st.code(..., height=<数>)`：代码块自己定高（折叠区里的日志全文）；
    - `st.code(..., height="content")` **且**外面包着一个定高的 st.container（2026-11-16 的
      日志尾部：容器定高并自动滚到底，里面的代码块随内容长）。光写 "content" 而没有定高的
      容器，就是老毛病换了个写法。

    扫整个 app/：日志区自 v0.2.2 M3 起在 app/pages_console.py（设计 §4 的拆分），
    只扫 dashboard.py 会得到空列表，这条断言就变成空跑。"""
    seen = 0
    for p in APP_FILES:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        boxed = {id(n) for w in _fixed_height_containers(tree) for n in ast.walk(w)}
        for call in ast.walk(tree):
            if not (isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "code"):
                continue
            seen += 1
            heights = [kw.value for kw in call.keywords if kw.arg == "height"]
            assert heights, f"{p.name} L{call.lineno} 的 st.code 没给 height（日志区会无限长）"
            grows = isinstance(heights[0], ast.Constant) and heights[0].value == "content"
            assert not grows or id(call) in boxed, \
                f"{p.name} L{call.lineno} 的 st.code 随内容长，外面却没有定高的容器"
    assert seen, "面板里没有 st.code 调用？日志区没了"


def test_the_log_tail_follows_the_newest_line(tmp_path):
    """摊在卡片上的日志尾部要**自动滚到底**：改之前它从头显示，30 行里只看得见开头九行，
    而要看的恰恰是最后一行（运行中是最新进度，没跑成是 traceback 的那句结论）。
    容器的定高与自动滚动都在元素树里看得见（block.proto），所以在运行时断言。"""
    _fake_run(tmp_path, "market_scan", "running", log="\n".join(SCAN_LOG.splitlines()[:20]))
    at = _page(tmp_path, "任务控制台")
    assert not at.exception, at.exception
    ui = app_module("ui")
    followers = [b for b in _descend(at.main)
                 if getattr(b, "type", "") == "flex_container" and b.proto.autoscroll]
    assert len(followers) == 1, f"日志尾部应恰好有一个自动滚到底的容器，实际 {len(followers)} 个"
    assert followers[0].proto.height_config.pixel_height == ui.LOG_BOX_HEIGHT, \
        "自动滚动只在定高的容器里才生效"
    codes = [e.value for e in _descend(followers[0]) if getattr(e, "type", "") == "code"]
    assert len(codes) == 1 and codes[0].endswith("[1800/3010] 信号 58 条，失败 0 只，耗时 673s"), codes


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
    """三件套齐全但 metrics.json 只写了一半：先说怎么办（重跑），再指名残缺目录
    （错误信息是唯一允许出现目录名的地方），不崩页。"""
    run = tmp_path / "output" / "ma_cross_20260824_151600"
    run.mkdir(parents=True)
    (run / "metrics.json").write_text('{"total_return": 0.48', encoding="utf-8")
    (run / "report.html").write_text("<html></html>", encoding="utf-8")
    (run / "trades.csv").write_text(TRADES_HEADER, encoding="utf-8")
    at = _page(tmp_path)
    assert not at.exception, at.exception
    errors = [e.value for e in at.error]
    assert any("重跑" in e and "删掉" in e and run.name in e for e in errors), errors


def test_empty_output_still_guides_the_user(tmp_path):
    """空 output/：四页都不许崩，且该给的提示都在（任务条也还在）。"""
    for page in PAGES:
        at = _page(tmp_path, page)
        assert not at.exception, f"{page}: {at.exception}"


# ================================================================ 标的清单的来源（v0.5.0）

def _ui_module():
    """单独加载 app/ui.py（run_symbols 是纯函数，不需要 Streamlit 上下文）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("qd_ui_symbols", ROOT / "app" / "ui.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["qd_ui_symbols"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_run_symbols_prefers_the_config_snapshot(tmp_path):
    """标的清单**首选** config_snapshot.json 的 universe——那是这趟回测真正跑的那批。

    v0.5.0 起单标的 K 线图默认不落盘（一次回测因此从 53 MB 降到 5.2 MB，实测），
    所以不能再靠 kline_*.html 的文件名列标的。
    """
    ui_mod = _ui_module()
    run = tmp_path / "ma_cross_20260902_120000"
    run.mkdir()
    (run / "config_snapshot.json").write_text(
        json.dumps({"universe": ["601899", "000333", "600519"]}), encoding="utf-8")
    assert ui_mod.run_symbols(run) == ["000333", "600519", "601899"]


def test_run_symbols_falls_back_to_kline_filenames_for_old_runs(tmp_path):
    """v0.5.0 之前的产物（本机有 58 个）没有别的清单来源，只能按文件名兜底。
    快照损坏时走同一条路——不能因为一个坏 JSON 就让这一页没得看。"""
    ui_mod = _ui_module()
    run = tmp_path / "old_run"
    run.mkdir()
    (run / "kline_600519.html").write_text("<html></html>", encoding="utf-8")
    (run / "kline_000333.html").write_text("<html></html>", encoding="utf-8")
    assert ui_mod.run_symbols(run) == ["000333", "600519"]

    (run / "config_snapshot.json").write_text("{ broken", encoding="utf-8")
    assert ui_mod.run_symbols(run) == ["000333", "600519"], "快照损坏时该退回文件名"


def test_run_symbols_returns_empty_rather_than_inventing_one(tmp_path):
    """两条路都空就返回空——编一个清单出来会让人对着一只根本没回测过的票看图。"""
    ui_mod = _ui_module()
    run = tmp_path / "empty_run"
    run.mkdir()
    assert ui_mod.run_symbols(run) == []


# ================================================================ 页头右端的「帮助中心」（2026-09-30 设计）
HELP_CENTER = "帮助中心"
NON_MANUAL_PAGES = ["市场概览", "任务控制台", "信号", "记账", "持仓与盈亏", "信号池", "回测报告", "个股K线"]


@pytest.mark.parametrize("page", NON_MANUAL_PAGES)
def test_every_page_has_a_help_center_link_on_its_title_row(tmp_path, page):
    """手册不在侧边栏里了（它不是系统的功能），入口放在每页页名那一行的最右端。
    同一行的证据：页头 HTML 与链接是**同一组分栏**的第 1、2 栏（4/5 与 1/5 宽）——
    只看"某个 4/5 栏装页头、某个 1/5 栏装链接"的话，链接挪到页头下面另开一组 [4,1] 也会过。
    另钉三处排版：两栏顶端对齐（居中会把链接压到页名下方 11–21 px）、栏距 small、链接在右栏里靠右。"""
    at = _page(tmp_path, page)
    assert not at.exception, at.exception
    links = [el for el in at.get("page_link") if el.proto.label == HELP_CENTER]
    assert len(links) == 1, [el.proto.label for el in at.get("page_link")]
    assert links[0].proto.page == "guide", links[0].proto
    cols = at.get("column")
    head_cols = [c for c in cols if any('class="qd-head"' in b for b in _bodies_under(c))]
    link_cols = [c for c in cols if any(getattr(e, "proto", None) is links[0].proto for e in _descend(c))]
    assert len(head_cols) == 1 and len(link_cols) == 1, (len(head_cols), len(link_cols))
    assert abs(head_cols[0].proto.weight - 0.8) < 1e-6, head_cols[0].proto.weight
    assert abs(link_cols[0].proto.weight - 0.2) < 1e-6, link_cols[0].proto.weight
    rows = [list(b.children.values()) for b in _descend(at._tree)
            if getattr(b, "children", None)   # 叶子元素没有 children，直接取会从 proto 上抛 AttributeError
            and all(getattr(k, "type", None) == "column" for k in b.children.values())
            and any('class="qd-head"' in x for x in _bodies_under(b))
            and any(getattr(e, "proto", None) is links[0].proto for e in _descend(b))]
    assert len(rows) == 1 and rows[0][0] is head_cols[0] and rows[0][1] is link_cols[0], len(rows)
    assert head_cols[0].gap == "small", head_cols[0].gap
    assert head_cols[0].proto.vertical_alignment == BlockProto.Column.VerticalAlignment.TOP, \
        head_cols[0].proto
    box = [e for e in _descend(link_cols[0]) if type(e).__name__ == "Block"]
    assert box and box[0].proto.flex_container.justify == BlockProto.FlexContainer.Justify.JUSTIFY_END, \
        [b.proto for b in box]


@pytest.mark.parametrize("page", NON_MANUAL_PAGES + ["使用说明", "读懂回测", "边界与安全"])
def test_page_head_lead_is_plain_text(tmp_path, page):
    """页头 HTML 会转义文字：说明里写 ** 会原样显示两对星号（记账页 2026-09-30 真机确认过）。"""
    at = _page(tmp_path, page)
    head = [h for h in _htmls(at) if 'class="qd-head"' in h][0]
    assert "**" not in head, head


# ================================================================ 按钮规范（2026-11-16）

#: 过去拼在按钮文字前面充当图标的那些字符。
_GLYPHS = "▶⏹■↻↶＋−+✔✓↓⬇"


def _leading_text(label: ast.expr) -> str | None:
    """按钮第一个实参（文字）开头那段**字面量**：纯字符串或 f-string 的首段；别的写法返回 None。"""
    if isinstance(label, ast.Constant) and isinstance(label.value, str):
        return label.value
    if isinstance(label, ast.JoinedStr) and label.values \
            and isinstance(label.values[0], ast.Constant):
        return str(label.values[0].value)
    return None


def test_buttons_carry_icons_as_icons_not_as_leading_characters():
    """按钮上的图标一律走 `icon=`（与侧栏导航同一套 Material 图标），不许再把 ▶ ⏹ ↻ ＋ ↶
    这类字符拼在文字前面：字符的粗细与基线跟着系统字体走，同一排里有的是字符、有的是图标，
    就是两套东西。表格里每行的「＋ 加入」「− 移除」不在此列——那是表格列（ButtonColumn）
    的文字，不是按钮控件，没有 icon 参数可用。"""
    seen = 0
    for p in APP_FILES:
        for call in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            # download_button 也算（2026-11-16：记账页的导出按钮曾把 ↓ 拼在文字前面）。
            # 注意它只看得见**字面量**：那两个导出按钮的字是变量（journal_ui.export_payloads 给的），
            # 这里扫不到——它们由 test_dashboard_journal 里钉住按钮文字的那一条管。
            if not (isinstance(call, ast.Call)
                    and getattr(call.func, "attr", "") in ("button", "download_button")
                    and call.args):
                continue
            seen += 1
            text = _leading_text(call.args[0])
            assert not (text and text[0] in _GLYPHS), \
                f"{p.name} L{call.lineno}: 按钮文字以字符图标开头 {text!r}，改用 icon="
    assert seen >= 10, f"只扫到 {seen} 个按钮调用，像是扫描本身出了问题"


# ================================================================ 操作按钮一律靠右（2026-11-16）
#
# 用户的话：「记这一笔」在左下角，其他页面的一些按钮在右下角，很不统一。现在全站一条规矩：
# 操作按钮靠右——表单的提交按钮在表单右下角，任务卡片 / 任务条的开始按钮在那一行的右端，
# 运行中的「停止」在进度条右边。AppTest 没有像素，看的是元素树里的排版（conftest.sits_on_the_right）。

#: 侧栏里的八个功能页（手册三页没有按钮）。
FUNCTION_PAGES = ["市场概览", "任务控制台", "信号", "信号池", "记账", "持仓与盈亏", "回测报告", "个股K线"]


def _with_settings(tmp_path: Path) -> Path:
    """给 tmp 里放一份真的 settings.yaml：没有它，「记账」页不给录入表单、「信号池」页整页降级，
    这两页上的按钮就一个都数不到。"""
    import shutil
    (tmp_path / "config").mkdir(exist_ok=True)
    shutil.copy(ROOT / "config" / "settings.yaml", tmp_path / "config" / "settings.yaml")
    return tmp_path


@pytest.mark.parametrize("page", FUNCTION_PAGES)
def test_every_action_button_sits_on_the_right(tmp_path, page):
    """谁新加一个按钮顺手放在左边，这条就红。"""
    at = _page(_with_settings(tmp_path), page)
    assert not at.exception, at.exception
    for key in action_button_keys(at):
        assert sits_on_the_right(at, key), f"{page} 页的按钮 {key} 不在右边"


def test_the_button_sweep_really_sees_the_buttons(tmp_path):
    """上一条的哨兵：页面上要是一个按钮都没数到，那条就是永远通过的空跑。"""
    _with_settings(tmp_path)
    seen = {page: action_button_keys(_page(tmp_path, page))
            for page in ("市场概览", "任务控制台", "信号", "信号池", "记账")}
    assert seen["市场概览"] == ["home_breadth_start"], seen
    assert seen["信号池"] == ["universe_load_pool"], seen
    assert seen["信号"] == ["bar_start_daily_signal", "bar_start_market_scan"], seen
    assert {"trial_market_scan", "start_market_scan", "rerun_market_scan",
            "start_backtest", "rerun_backtest"} <= set(seen["任务控制台"]), seen
    assert {"journal_submit", "journal_other_submit", "journal_restore"} <= set(seen["记账"]), seen


def test_the_right_side_check_can_tell_left_from_right(tmp_path):
    """助手自己的哨兵：帮助中心那个链接所在的容器靠右，记账页的输入框不靠右。
    它要是对什么都答"靠右"，上面两条就都成了空跑。"""
    at = _page(_with_settings(tmp_path), "记账")
    assert sits_on_the_right(at, "journal_submit")
    assert not sits_on_the_right(at, "journal_symbol"), "第一栏里的输入框不该算靠右"
    assert not sits_on_the_right(at, "journal_kind"), "直接放在卡片里的切换不该算靠右"


@pytest.mark.parametrize("page, job, stop_key", [
    ("任务控制台", "market_scan", "stop_market_scan"),
    ("信号", "market_scan", "bar_stop_market_scan"),
    ("回测报告", "backtest", "bar_stop_backtest"),
    ("市场概览", "market_breadth", "home_breadth_stop"),
])
def test_the_stop_button_sits_right_of_the_progress_wherever_a_job_runs(tmp_path, page, job, stop_key):
    """运行中的「停止」三处一个画法（ui.running_row）：进度在左、停止在同一行的右端，
    悬停说明都是那句"关掉浏览器也不会中断"。改之前任务条上的在最左、首页的单独在进度条下面。"""
    _fake_run(tmp_path, job, "running", log="login success!\n")
    at = _page(tmp_path, page)
    assert not at.exception, at.exception
    assert stop_key in action_button_keys(at), action_button_keys(at)
    assert sits_on_the_right(at, stop_key), f"{page} 页的「停止」不在右边"
    stop = at.button(stop_key)
    assert stop.label == "停止" and stop.proto.icon == ":material/stop:"
    guide = app_module("guide")
    assert stop.proto.help == guide.stop_help(job) and guide.CONSOLE_BACKGROUND_NOTE in stop.proto.help


# ---------------------------------------------------------------- 助手自己得靠得住
# 独立审查举了三个反例：初稿的 sits_on_the_right 只看按钮自己那一行靠不靠右，不看这一行本身
# 在不在右边——靠右对齐的容器若在左边那一栏里，它也答"靠右"。下面是那三个反例与几个正例。

def _layout_left_column_right_aligned():
    import streamlit as st
    left, right = st.columns([1, 3])
    with left.container(horizontal=True, horizontal_alignment="right"):
        st.button("x", key="k")          # 只到整行四分之一处
    right.write("右边还有四分之三")


def _layout_two_right_aligned_columns():
    import streamlit as st
    a, b = st.columns([1, 1])
    with a.container(horizontal=True, horizontal_alignment="right"):
        st.button("mid", key="mid")      # 只到整行一半处
    with b.container(horizontal=True, horizontal_alignment="right"):
        st.button("end", key="end")


def _layout_narrow_column_in_the_left_half():
    import streamlit as st
    left, right = st.columns([1, 1])
    _bar, stop = left.columns([5, 1])
    stop.button("stop", key="stop")
    right.write("右半边")


def _layout_the_shapes_the_panel_uses():
    import streamlit as st
    with st.container(horizontal=True, horizontal_alignment="right"):
        st.button("plain", key="plain")
    with st.container(horizontal=True, horizontal_alignment="right"):
        with st.container(horizontal=True, width="content"):
            st.button("grouped", key="grouped")
    _bar, stop = st.columns([5, 1])
    stop.button("narrow", key="narrow")
    _a, b = st.columns(2)
    _c, d = b.columns(2)
    with d.container(horizontal=True, horizontal_alignment="right"):
        st.button("nested", key="nested")
    with st.container(border=True):
        st.button("bare", key="bare")
    with st.container(horizontal=True):
        st.button("leftrow", key="leftrow")
    _wide, also = st.columns([1, 1])
    also.button("widecol", key="widecol")


@pytest.mark.parametrize("layout, key, expected", [
    (_layout_left_column_right_aligned, "k", False),
    (_layout_two_right_aligned_columns, "mid", False),
    (_layout_two_right_aligned_columns, "end", True),
    (_layout_narrow_column_in_the_left_half, "stop", False),
    (_layout_the_shapes_the_panel_uses, "plain", True),      # 靠右对齐的横向容器
    (_layout_the_shapes_the_panel_uses, "grouped", True),    # 里面再包一组随内容宽的
    (_layout_the_shapes_the_panel_uses, "narrow", True),     # 5:1 里的那个 1
    (_layout_the_shapes_the_panel_uses, "nested", True),     # 嵌套分栏，层层都是最后一栏
    (_layout_the_shapes_the_panel_uses, "bare", False),      # 直接放在卡片里
    (_layout_the_shapes_the_panel_uses, "leftrow", False),   # 横向容器但靠左
    (_layout_the_shapes_the_panel_uses, "widecol", False),   # 最后一栏但栏很宽，按钮在它左缘
])
def test_sits_on_the_right_tells_right_from_not_right(layout, key, expected):
    at = AppTest.from_function(layout).run()
    assert not at.exception, at.exception
    assert sits_on_the_right(at, key) is expected
