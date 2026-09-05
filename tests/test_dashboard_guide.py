# tests/test_dashboard_guide.py — v0.2.1 M3「使用说明」页与就地帮助的**渲染层**
# （离线，AppTest + 源码断言）
#
# 文案本身在 tests/test_guide.py 里逐条对账过；这里只管"有没有真的渲染出来、
# 挂在对的位置上"。三类容易静默失效的接线：
#   - 说明页在「手册」组第一位（2026-09-05 起落地页是任务控制台，见 test_dashboard_nav.py）；
#   - 每张任务卡片右上角的 st.popover，以及每个参数控件的 help=
#     （漏挂不会报错，只是 tooltip 永远不出现）；
#   - 空态文案与表格上方那行灰字（退回"暂无数据"也照样能跑）。
import ast
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from quant.runner import jobs
from tests.conftest import APP_FILES, copy_app, goto_page

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "app" / "dashboard.py"
SOURCE = DASHBOARD.read_text(encoding="utf-8")
UI_SOURCE = (ROOT / "app" / "ui.py").read_text(encoding="utf-8")

GUIDE = "使用说明"
# 侧栏顺序自 v0.2.2 §2.2 起：控制台提到第二位。v0.3.1 M1 起「交易日志」拆成
# 「记账」与「持仓与盈亏」两个子页（侧栏成组）。页表本身（分组/顺序/图标/URL）
# 由 tests/test_dashboard_nav.py 对账，这里只借它来遍历"每页都要成立"的断言。
# v0.5.0：说明拆成四页，后三页单独成「手册」组，紧跟主组。
# 2026-09-05：「自定义策略」搬去 docs/，手册组剩两页。
PAGES = [GUIDE, "读懂回测", "边界与安全",
         "任务控制台", "信号", "记账", "持仓与盈亏",
         "信号池", "回测报告", "个股K线"]

# 先塞 sys.modules 再 exec：guide.py 用了 @dataclass，而 dataclasses 会回查
# sys.modules[cls.__module__] 解析注解，没注册会 AttributeError。
_SPEC = importlib.util.spec_from_file_location("qd_guide_ui", ROOT / "app" / "guide.py")
guide = importlib.util.module_from_spec(_SPEC)
sys.modules["qd_guide_ui"] = guide
_SPEC.loader.exec_module(guide)

SCAN_HEADER = "date,symbol,name,strategy,close,pct_chg,amount,amount_ratio_20d\n"
TRADES_HEADER = "symbol,action,date,price,shares,commission,stamp,pnl,holding_days\n"


def _page(tmp_path: Path, page: str = GUIDE) -> AppTest:
    """app/ 整目录复制进 tmp_path 再交给 AppTest（ROOT/OUTPUT 全落在 tmp_path）。
    一律显式切页：默认落地页是「任务控制台」，靠默认值取页会静默测错页面。"""
    return goto_page(
        AppTest.from_file(str(copy_app(tmp_path)), default_timeout=30).run(), page)


def _htmls(at: AppTest) -> list[str]:
    """页面上我们自己包的 .qd-* HTML；theme.inject() 那段 <style> 要滤掉
    （CSS 里出现每一个类名，不滤的话结构断言永远为真）。"""
    return [b for e in at.get("html")
            if not (b := e.proto.body).lstrip().startswith("<style>")]


def _markdowns(at: AppTest) -> str:
    return "\n".join(m.value for m in at.main.markdown)


def _descend(block):
    """元素树递归展开（AppTest 只给顶层容器，列布局要自己走下去）。"""
    yield block
    for child in getattr(block, "children", {}).values():
        yield from _descend(child)


def _bodies_under(block) -> list[str]:
    return [b for e in _descend(block)
            if (b := getattr(getattr(e, "proto", None), "body", None))]


def _popover_label(block) -> str:
    """AppTest 里 popover 是个 Block，标签在 proto.popover.label（没有 .label）。"""
    return block.proto.popover.label


def _run_dir(root: Path, name: str = "ma_cross_20260826_112606", *,
             trades: str = "", skipped: str = "") -> Path:
    run = root / "output" / name
    run.mkdir(parents=True, exist_ok=True)
    (run / "metrics.json").write_text('{"n_trades": 243}', encoding="utf-8")
    (run / "report.html").write_text("<html></html>", encoding="utf-8")
    (run / "trades.csv").write_text(TRADES_HEADER + trades, encoding="utf-8")
    if skipped:
        (run / "skipped.csv").write_text("date,symbol,reason\n" + skipped,
                                         encoding="utf-8")
    return run


def _fake_run(root: Path, job: str, status: str, *, log: str = "",
              exit_code=None) -> None:
    runs = root / "output" / "runs"
    (runs / "logs").mkdir(parents=True, exist_ok=True)
    log_path = runs / "logs" / f"{job}.log"
    log_path.write_text(log, encoding="utf-8")
    (runs / f"{job}.json").write_text(json.dumps({
        "script": job, "run_id": f"{job}_20260825_100000_000", "pid": os.getpid(),
        "argv": [sys.executable, "-u", jobs.JOBS[job].script],
        "log_path": str(log_path),
        "started_at": (datetime.now() - timedelta(minutes=3)).isoformat(
            timespec="seconds"),
        "status": status, "exit_code": exit_code,
        "finished_at": None if status == "running"
        else datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False), encoding="utf-8")


# ================================================================ 侧栏与落地页

def test_the_guide_is_no_longer_the_landing_page(tmp_path):
    """2026-09-05 用户定：落地页换成「任务控制台」（v0.2.1～v0.5.0 是这一页）。
    不切页渲染出来的不再是说明页；说明页仍可显式切到（其余测试都这么切）。
    第一次用的人怎么找到它：控制台在从未跑过任务时给链接（tests/test_dashboard_console.py）。"""
    at = AppTest.from_file(str(copy_app(tmp_path)), default_timeout=30).run()
    assert not at.exception, at.exception
    heads = [h for h in _htmls(at) if 'class="qd-head"' in h]
    assert len(heads) == 1, heads
    assert f'class="qd-title">{GUIDE}<' not in heads[0], heads[0]
    at = goto_page(at, GUIDE)
    heads = [h for h in _htmls(at) if 'class="qd-head"' in h]
    assert f'class="qd-title">{GUIDE}<' in heads[0], heads[0]


def test_the_sidebar_carries_only_the_disclaimer(tmp_path):
    """2026-09-05：侧栏只留导航 + 免责声明。原先常驻的"第一次用先看使用说明"
    与"本面板可在本机启动任务，详情见控制台"都是在讲面板自己，删了。
    安全警告只在监听地址不是本机时出现（见 test_dashboard_control_bar.py）。"""
    at = _page(tmp_path, "任务控制台")
    captions = [c.value for c in at.sidebar.caption]
    assert len(captions) == 1 and "投资建议" in captions[0], captions
    assert GUIDE not in captions[0]


# ================================================================ 说明页本体

def test_guide_page_renders_without_exception(tmp_path):
    at = _page(tmp_path)
    assert not at.exception, at.exception


def test_guide_page_has_a_page_head_like_every_other_page(tmp_path):
    """§2.4 通用页头对说明页同样适用（否则六页里有一页开局长得不一样）。"""
    at = _page(tmp_path)
    heads = [h for h in _htmls(at) if 'class="qd-head"' in h]
    assert len(heads) == 1, heads
    assert f'class="qd-title">{GUIDE}<' in heads[0], heads[0]


def test_every_section_of_the_guide_gets_a_serif_heading(tmp_path):
    """每节各出一个 .qd-section 小标题，顺序与 guide.SECTIONS 一致。

    v0.5.0 起十节分在四页上（设计 §5），所以逐页收集再拼起来对账：
    **每一节恰好出现一次**，不重不漏、不错位。这条比拆分前更值钱——
    拆页最容易犯的错就是漏掉一节，而漏掉之后页面照常渲染、没有任何报错。
    """
    seen = []
    for page in guide.GUIDE_PAGES:
        at = _page(tmp_path, page.title)
        titles = [h for h in _htmls(at) if 'class="qd-section"' in h]
        # 落地页尾多一个「更多说明」的目录小标题，它不是正文节
        seen += [h for h in titles if guide.GUIDE_MORE_TITLE not in h]
    assert len(seen) == len(guide.SECTIONS), \
        f"四页加起来应有 {len(guide.SECTIONS)} 个小标题，实际 {len(seen)}"
    for html, sec in zip(seen, guide.SECTIONS):
        assert sec.title in html, f"小标题错位: {html} vs {sec.title}"


def test_every_section_body_is_on_some_guide_page(tmp_path):
    """十节正文一节不少地落在四页里的某一页上。"""
    blob = ""
    for page in guide.GUIDE_PAGES:
        at = _page(tmp_path, page.title)
        blob += _markdowns(at) + "\n" + " ".join(w.value for w in at.main.warning) + "\n"
    for sec in guide.SECTIONS:
        head = sec.body.strip().splitlines()[0]
        assert head in blob, f"「{sec.title}」的正文在四页里都没渲染出来: {head!r}"


def test_the_landing_page_links_to_the_other_pages(tmp_path):
    """落地页页尾的目录：其余各页各一个链接。

    拆页最大的风险是"另外几页没人找得到"。侧栏里有它们，但落地页也必须有
    一条明路——第一次用的人是从这一页开始读的。
    """
    at = _page(tmp_path)
    # AppTest 不给 page_link 建模（拿到的是 UnknownElement），但 .proto 里有 label。
    # 走 proto 而不是"页面上有没有这三个字"：后者在正文里随便提一句也能蒙混过关。
    links = [el.proto for el in at.get("page_link")]
    assert [pb.label for pb in links] == [p.title for p in guide.GUIDE_PAGES[1:]], links
    assert all(pb.help for pb in links), "每个链接都要有一句「什么时候需要点进去」"


def test_the_toc_uses_page_links_not_buttons(tmp_path):
    """目录用 st.page_link 而**不是** st.button。

    这不是口味：在这个面板里按钮意味着"会起进程"（上面那条逐页钉着一个都不许有），
    而 page_link 只导航、不触发任何任务。
    """
    at = _page(tmp_path)
    assert len(at.get("page_link")) == len(guide.GUIDE_PAGES) - 1


def test_the_closed_loop_diagram_is_rendered(tmp_path):
    """§3.1 第 1 条那张闭环图（扫描发现 → 加入 universe → 信号跟踪跟踪卖出）。"""
    at = _page(tmp_path)
    flows = [h for h in _htmls(at) if 'class="qd-flow"' in h]
    assert len(flows) == 1, f"应有一张闭环图，实际 {len(flows)}"
    for step in guide.section("tasks").flow:
        assert step in flows[0], f"闭环图缺一步「{step}」: {flows[0]}"


def test_the_safety_block_is_a_warning_not_plain_text(tmp_path):
    """§3.1 第 7 条：单独成块、醒目。混在正文 markdown 里就不叫醒目了。

    v0.5.0 起安全提示落在「边界与安全」页（设计 §5 的分页表）。
    """
    at = _page(tmp_path, guide.guide_page("guide_limits").title)
    warnings = [w.value for w in at.main.warning]
    assert len(warnings) == 1, f"该页应恰好一个警告块，实际 {len(warnings)}"
    assert "局域网" in warnings[0], warnings[0]


@pytest.mark.parametrize("page", [p.title for p in guide.GUIDE_PAGES])
def test_only_the_safety_section_is_ever_a_warning(tmp_path, page):
    """到处都是黄框等于没有黄框——**全手册恰好一个**，在「边界与安全」页上。
    拆页之后这条得逐页查：任何一页多冒一个黄框，那唯一一个就不再唯一。"""
    at = _page(tmp_path, page)
    expected = 1 if page == guide.guide_page("guide_limits").title else 0
    assert len(at.main.warning) == expected, \
        f"{page} 页的警告块数应为 {expected}，实际 {len(at.main.warning)}"


@pytest.mark.parametrize("page", [p.title for p in guide.GUIDE_PAGES])
def test_no_guide_page_has_start_buttons(tmp_path, page):
    """手册**每一页**都是纯文档：不放控制条（这些页没有任何产物可看，
    也不该让人在读说明时误点一次 18 分钟的全量扫描）。

    v0.5.0 拆页后必须逐页查：只查落地页的话，新拆出来的三页可以随便加按钮。
    """
    at = _page(tmp_path, page)
    assert at.main.button == [], [b.label for b in at.main.button]


def test_opening_the_guide_page_starts_no_process(tmp_path, monkeypatch):
    """与控制台页同一条铁律：光是打开页面绝不能起进程。"""
    from quant.runner import process
    calls = []
    monkeypatch.setattr(process, "start", lambda *a, **k: calls.append(a))
    _page(tmp_path)
    assert calls == []


def test_the_guide_page_needs_no_output_at_all(tmp_path):
    """output/ 全空（刚 clone 下来的样子）时说明页必须照常可读——
    它恰恰是这时候最该看的一页。"""
    at = _page(tmp_path)
    assert not at.exception, at.exception
    assert at.main.error == [], [e.value for e in at.main.error]


@pytest.mark.parametrize("page", PAGES)
def test_all_six_pages_render_without_exception(tmp_path, page):
    """v0.2.2 M3 验收（设计 §5）：六页全绿（「信号池」是新的第四页）。"""
    at = _page(tmp_path, page)
    assert not at.exception, f"页面 {page} 抛异常: {at.exception}"


@pytest.mark.parametrize("page", PAGES)
def test_all_six_pages_render_while_a_job_is_running(tmp_path, page):
    _fake_run(tmp_path, "market_scan", "running", log="[1800/3010] 信号 58 条\n")
    at = _page(tmp_path, page)
    assert not at.exception, f"页面 {page} 在有任务运行时抛异常: {at.exception}"


@pytest.mark.parametrize("page", PAGES)
def test_all_six_pages_survive_a_corrupt_state_file(tmp_path, page):
    """状态文件坏掉时六页都要活着（说明页也读它——页头的全局 pill）。"""
    runs = tmp_path / "output" / "runs"
    runs.mkdir(parents=True)
    (runs / "backtest.json").write_text('{"script": "backtest", "pid":',
                                        encoding="utf-8")
    at = _page(tmp_path, page)
    assert not at.exception, f"页面 {page} 被损坏状态文件崩了: {at.exception}"


def test_page_intro_covers_every_page(tmp_path):
    """PAGE_INTRO 缺一页就是 KeyError 崩页（页头直接下标取值）。

    自 v0.2.2 M3 起 PAGE_INTRO 与页头一起在共享件 app/ui.py（设计 §4 的拆分）。
    """
    tree = ast.parse(UI_SOURCE)
    intro = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.Assign)
                 and any(getattr(t, "id", "") == "PAGE_INTRO" for t in n.targets))
    keys = [k.value for k in intro.value.keys]
    assert keys == PAGES, f"PAGE_INTRO 的键必须与侧栏页表一致: {keys}"


# ================================================================ 就地帮助：卡片 popover

#: 控制台上带 `?` 的卡片数：三个任务 + v0.5.0 的「加进信号池」人工步骤。
#: 人工那一步**尤其**需要帮助——它是唯一一张没有按钮的卡片，不解释就像是坏了。
CARDS_WITH_HELP = len(jobs.JOBS) + 1


def test_every_card_has_a_help_popover(tmp_path):
    """§3.2：每张卡片右上角一个 st.popover("?")。"""
    at = _page(tmp_path, "任务控制台")
    assert not at.exception, at.exception
    labels = [_popover_label(p) for p in at.get("popover")]
    assert len(labels) == CARDS_WITH_HELP, f"应各有一个 popover，实际 {labels}"


def test_the_popover_content_is_this_jobs_help(tmp_path):
    """popover 里必须是**这个**任务的帮助文案，不能三张卡片抄同一段。"""
    at = _page(tmp_path, "任务控制台")
    blob = _markdowns(at)
    for name, text in guide.JOB_HELP.items():
        head = text.strip().splitlines()[0]
        assert head in blob, f"{name} 的帮助没渲染出来: {head!r}"


def test_the_popover_sits_next_to_the_card_title(tmp_path):
    """「右上角」不是装饰：帮助要跟标题同一行。挤到参数控件下面就排在按钮后面，
    正好在"不知道这按钮会干什么"的那一刻看不见。

    结构证据：标题行是 st.columns([5, 1])，宽列里是 .qd-section 小标题，
    窄列里只有那个 popover。只断言标签是 "?" 证明不了它在哪。
    """
    at = _page(tmp_path, "任务控制台")
    pops = at.get("popover")
    assert len(pops) == CARDS_WITH_HELP, f"应有 {CARDS_WITH_HELP} 个 popover，实际 {len(pops)}"
    for pop in pops:
        assert _popover_label(pop) == "?", _popover_label(pop)
    narrow = [c for c in at.get("column")
              if any(b.proto is p.proto for b in _descend(c) for p in pops)
              and abs(c.proto.weight - 1 / 6) < 1e-6]
    assert len(narrow) == CARDS_WITH_HELP, \
        f"每个 popover 都该待在标题行的窄列里，实际 {len(narrow)}"
    titled = [c for c in at.get("column")
              if abs(c.proto.weight - 5 / 6) < 1e-6
              and any('class="qd-section"' in b for b in _bodies_under(c))]
    assert len(titled) == CARDS_WITH_HELP, \
        f"每个卡片标题都该在宽列里与 ? 同行，实际 {len(titled)}"


def test_control_bar_pages_have_no_popover(tmp_path):
    """精简控制条要越薄越好（§4.2）：帮助只挂在控制台的完整卡片上。"""
    for page in ("回测报告", "个股K线", "信号"):
        at = _page(tmp_path, page)
        assert at.get("popover") == [], f"{page} 的控制条不该有 popover"


# ================================================================ 就地帮助：参数 tooltip

def test_every_param_widget_carries_its_tooltip(tmp_path):
    """§3.2：参数控件挂 help=。漏挂不报错，只是 tooltip 永远不出现——
    所以只能在这里逐个比对 proto.help。"""
    at = _page(tmp_path, "任务控制台")
    assert not at.exception, at.exception
    seen: dict[str, str] = {}
    for kind in ("number_input", "date_input", "selectbox", "checkbox"):
        for el in at.get(kind):
            # proto.help 是没有 presence 的普通 string 字段（HasField 会 ValueError），
            # 没挂 help 时就是空串
            if el.proto.help:
                seen[el.proto.help] = kind
    for (job_name, param), text in guide.PARAM_HELP.items():
        assert text in seen, f"{job_name}.{param} 的控件没挂上 tooltip: {text!r}"


def test_no_param_widget_is_left_without_help(tmp_path):
    """反向：控件多了一个而 PARAM_HELP 没跟上时，这条会红。"""
    at = _page(tmp_path, "任务控制台")
    widgets = [el for kind in ("number_input", "date_input", "checkbox")
               for el in at.get(kind)]
    # 策略下拉之外还有"选择回测/选择标的"等只读页控件，所以只数控制台页的
    for el in widgets:
        assert el.proto.help.strip(), f"控件 {el.proto.label!r} 没有 help"


# ================================================================ 就地帮助：表格灰字

def test_trades_table_has_a_column_hint_above_it(tmp_path):
    _run_dir(tmp_path, trades="000333,buy,2016-04-05,10.0,100,5.0,0.0,,\n")
    at = _page(tmp_path, "回测报告")
    captions = [c.value for c in at.main.caption]
    assert guide.TABLE_HINTS["trades"] in captions, captions


def test_skipped_table_has_its_own_hint(tmp_path):
    """被跳过的订单表解释的是 reason 列，跟成交明细不是一套话。"""
    _run_dir(tmp_path, trades="000333,buy,2016-04-05,10.0,100,5.0,0.0,,\n",
             skipped="2016-04-05,000001,涨停无法买入\n")
    at = _page(tmp_path, "回测报告")
    captions = [c.value for c in at.main.caption]
    assert guide.TABLE_HINTS["skipped"] in captions, captions


def test_scan_table_has_the_volume_ratio_hint(tmp_path):
    scan = tmp_path / "output" / "scan"
    scan.mkdir(parents=True)
    (scan / "2026-08-25.csv").write_text(
        SCAN_HEADER + "2026-08-25,000020,深华发A,ma_cross,11.74,-5.09,2.9e8,4.22\n",
        encoding="utf-8")
    at = _page(tmp_path, "信号")
    captions = [c.value for c in at.main.caption]
    assert guide.TABLE_HINTS["scan"] in captions, captions


def test_signal_table_has_its_own_hint(tmp_path):
    sig = tmp_path / "output" / "signals"
    sig.mkdir(parents=True)
    (sig / "2026-08-25.csv").write_text(
        "date,symbol,strategy,action,close\n2026-08-25,000333,ma_cross,buy,10.0\n",
        encoding="utf-8")
    at = _page(tmp_path, "信号")
    captions = [c.value for c in at.main.caption]
    assert guide.TABLE_HINTS["signal"] in captions, captions


def test_an_empty_table_shows_no_column_hint(tmp_path):
    """表格是空的时候还解释列，只是噪声。灰字跟着表格一起出现或一起消失。"""
    _run_dir(tmp_path)      # 零成交
    at = _page(tmp_path, "回测报告")
    captions = [c.value for c in at.main.caption]
    assert guide.TABLE_HINTS["trades"] not in captions, captions


# ================================================================ 空态文案（§3.2）

def test_backtest_empty_state_guides_instead_of_just_reporting(tmp_path):
    at = _page(tmp_path, "回测报告")
    infos = [i.value for i in at.main.info]
    assert guide.EMPTY_STATES["backtest"] in infos, infos


def test_kline_empty_state_reuses_the_same_wording(tmp_path):
    """K 线页读的是同一批回测产物，空态说法必须一致（两套话会让人以为是两回事）。"""
    at = _page(tmp_path, "个股K线")
    infos = [i.value for i in at.main.info]
    assert guide.EMPTY_STATES["backtest"] in infos, infos


def test_signal_empty_state_mentions_the_close_time(tmp_path):
    at = _page(tmp_path, "信号")
    infos = [i.value for i in at.main.info]
    assert guide.EMPTY_STATES["signal"] in infos, infos
    assert any(guide.FACTS["data_ready"] in i for i in infos), infos


def test_scan_empty_state_warns_how_long_a_full_run_takes(tmp_path):
    """全量扫描是挂机任务。空态不写耗时，用户点下去就以为面板卡死了。"""
    at = _page(tmp_path, "信号")
    infos = [i.value for i in at.main.info]
    assert guide.EMPTY_STATES["scan"] in infos, infos
    assert any(guide.FACTS["scan_minutes"] in i for i in infos), infos


def test_empty_states_are_not_hardcoded_in_the_dashboard(tmp_path):
    """空态文案只许来自 guide.EMPTY_STATES：面板里再手写一份，
    改文案时必然漏掉一处（本文件的其他断言只能看到被渲染的那一份）。

    只查**字符串字面量**（走 AST），不查整份源码：注释里引用一句文案来解释
    为什么这么排版是正当的，用裸文本搜索会把它一起误判。
    整个 app/ 一起查（M3 起页面代码在 pages_*.py，只查 dashboard.py 等于空跑），
    但 app/guide.py 本身要排除——文案的**唯一定义处**就在那里。
    """
    literals = [n.value
                for p in APP_FILES if p.name != "guide.py"
                for n in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    for phrase in ("暂无回测结果", "暂无信号记录", "暂无全市场扫描结果"):
        hits = [s for s in literals if phrase in s]
        assert hits == [], f"空态文案「{phrase}」应下沉到 app/guide.py: {hits}"


# ================================================================ 回归：既有行为

def test_half_written_run_dir_still_gets_the_friendly_hint(tmp_path):
    """v0.2.0 的修复必须活着：换了空态文案也不能让半截目录崩页。"""
    run = tmp_path / "output" / "ma_cross_20260824_151600"
    run.mkdir(parents=True)
    (run / "metrics.json").write_text('{"n_trades": 1}', encoding="utf-8")
    for page in ("回测报告", "个股K线"):
        at = _page(tmp_path, page)
        assert not at.exception, f"{page}: {at.exception}"
        assert at.main.info, f"{page} 应显示空态提示"


def test_stopped_run_still_shows_no_eta(tmp_path):
    """B1 回归：加了 popover 之后卡片重排过，进度那两行不能被带坏。"""
    _fake_run(tmp_path, "market_scan", "stopped", exit_code=-15,
              log="扫描池 3010 只\n[1800/3010] 信号 58 条，失败 0 只，耗时 900s\n")
    at = _page(tmp_path, "任务控制台")
    assert not at.exception, at.exception
    everything = " ".join([b.proto.text for b in at.get("progress")]
                          + [c.value for c in at.main.caption])
    assert "预计剩余" not in everything, everything
    assert "扫描中" not in everything, everything
