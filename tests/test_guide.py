# tests/test_guide.py — v0.2.1 M3「使用说明」页与就地帮助的**文案层**（纯字符串，无 UI）
#
# 这个文件守的是一条纪律：**说明里的每个数字都必须是实测的**。
# 说明页要讲"两个策略都跑输躺平"这种结论，编错一个百分点，用户就会照着一个不存在的
# 事实做判断——比崩页更糟（崩页看得见，假数字看不见）。做法是：
#   1. 所有数字集中在 guide.FACTS 一个字典里，正文只能通过它取值（不许就地硬写）；
#   2. 每个值都必须在 README.md 里逐字出现（README 是维护中的事实来源）；
#   3. 回测那几项还要落在 README **既有**的实测表格一节里——那一节不是本里程碑写的，
#      所以这条断言不是自说自话；
#   4. "回撤只有约 1/3" 这类**推导出来的**结论按数值验算，将来换数据池时会自己红。
import ast
import importlib.util
import re
import sys
from pathlib import Path

import pytest

from quant.runner import jobs

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")

def _load(name: str, filename: str):
    """按路径加载 app/ 下的模块（app/ 不是包，只有 `streamlit run` 会把它放进
    sys.path）。**必须先塞进 sys.modules 再 exec**：guide.py 里用了 @dataclass，
    而 dataclasses 会去 sys.modules 里回查 cls.__module__ 解析类型注解，
    没注册就直接 AttributeError: 'NoneType' object has no attribute '__dict__'。"""
    spec = importlib.util.spec_from_file_location(name, ROOT / "app" / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


guide = _load("qd_guide", "guide.py")
theme = _load("qd_theme_guide", "theme.py")


def _readme_section(title: str) -> str:
    """取 README 中某个标题（## 或 ###）到下一个同级/更高级标题之间的正文。
    与 tests/test_dashboard_control_bar.py 同一个解析口径。"""
    lines = README.splitlines()
    starts = [i for i, ln in enumerate(lines) if ln.startswith("#") and title in ln]
    assert starts, f"README 缺少「{title}」一节"
    i = starts[0]
    level = len(lines[i]) - len(lines[i].lstrip("#"))
    for j in range(i + 1, len(lines)):
        head = lines[j]
        if head.startswith("#") and (len(head) - len(head.lstrip("#"))) <= level:
            return "\n".join(lines[i + 1:j])
    return "\n".join(lines[i + 1:])


def _all_text() -> str:
    """说明页正文 + 全部就地帮助文案，拼成一整块用于全局断言。"""
    parts = [s.title for s in guide.SECTIONS] + [s.body for s in guide.SECTIONS]
    parts += [step for s in guide.SECTIONS for step in s.flow]
    parts += list(guide.JOB_HELP.values())
    parts += list(guide.PARAM_HELP.values())
    parts += list(guide.TABLE_HINTS.values())
    parts += list(guide.EMPTY_STATES.values())
    # 信号池页那两段（v0.2.2 M3 §3.4）：底部"改动写到哪里"的说明与候选清单的代价。
    parts += [guide.POOL_NOTE, guide.POOL_LOAD_HINT]
    parts.append(guide.PAGE_INTRO)
    return "\n".join(parts)


# ================================================================ 数字纪律

def test_facts_is_not_empty():
    """下面几条断言全靠遍历 FACTS，空字典会让它们变成永远通过的空跑。"""
    assert len(guide.FACTS) >= 15, f"FACTS 只有 {len(guide.FACTS)} 项，像是被清空了"


@pytest.mark.parametrize("key", sorted(guide.FACTS))
def test_every_fact_appears_verbatim_in_the_readme(key):
    """说明页与 README 不许各说一套。改了 README 的实测数字，这条会立刻红，
    提醒你同步说明页（反过来也一样）。"""
    value = guide.FACTS[key]
    assert value in README, (
        f"FACTS[{key!r}] = {value!r} 在 README.md 里找不到——"
        f"要么数字是编的，要么 README 该同步更新了")


@pytest.mark.parametrize("key", sorted(guide.FACTS))
def test_every_fact_is_actually_used_in_the_copy(key):
    """FACTS 不是摆设：留着没人用的「事实」会慢慢腐烂成假数字。"""
    assert guide.FACTS[key] in _all_text(), \
        f"FACTS[{key!r}] = {guide.FACTS[key]!r} 定义了却没在任何文案里用到"


BACKTEST_FACTS = ("ma_total", "ma_cagr", "ma_dd", "ma_sharpe", "ma_trades",
                  "dc_total", "dc_cagr", "dc_dd", "dc_sharpe", "dc_trades",
                  "hold_total", "hold_cagr", "hold_dd",
                  "csi300_total", "csi300_cagr", "csi300_dd",
                  "backtest_start", "backtest_end", "capital", "universe_n",
                  # —— v0.4 当前默认口径（三策略 + 双叠加层）与叠加层归因（M4）。
                  #    基线那组永不重算；这组随"真实重跑"更新，两套都得钉在
                  #    README「① 回测」一节里，防止说明页与 README 各说一套。
                  "v4_end",
                  "v4_ma_total", "v4_ma_cagr", "v4_ma_dd", "v4_ma_sharpe", "v4_ma_trades",
                  "v4_dc_total", "v4_dc_cagr", "v4_dc_dd", "v4_dc_sharpe", "v4_dc_trades",
                  "v4_ts_total", "v4_ts_cagr", "v4_ts_dd", "v4_ts_sharpe", "v4_ts_trades",
                  "v4_hold_total", "v4_hold_cagr", "v4_hold_dd",
                  "v4_csi300_total", "v4_csi300_cagr", "v4_csi300_dd",
                  "v4_ma_base", "v4_ma_gate", "v4_ma_stop",
                  "v4_dc_base", "v4_dc_gate", "v4_dc_stop",
                  "v4_ts_base", "v4_ts_gate", "v4_ts_stop",
                  "v4_ts_base_dd", "v4_lockout_max")


@pytest.mark.parametrize("key", BACKTEST_FACTS)
def test_backtest_facts_come_from_the_readme_measured_table(key):
    """回测那几项必须落在 README **既有**的「① 回测」一节里（含实测结果表格）。
    那一节不是本里程碑写的，所以这条不是自说自话——真在跟一份独立的记录对账。"""
    section = _readme_section("① 回测")
    assert guide.FACTS[key] in section, \
        f"FACTS[{key!r}] = {guide.FACTS[key]!r} 不在 README 的回测实测一节里"


SCAN_FACTS = ("scan_pool", "scan_seconds", "scan_minutes", "scan_signals",
              "liquidity_floor", "backtest_seconds")


@pytest.mark.parametrize("key", SCAN_FACTS)
def test_runtime_facts_come_from_the_readme_guide_section(key):
    """扫描池大小、实测耗时这些数字来自真实运行日志，落在 README 新增的说明页一节。"""
    section = _readme_section(guide.README_SECTION)
    assert guide.FACTS[key] in section, \
        f"FACTS[{key!r}] = {guide.FACTS[key]!r} 不在 README「{guide.README_SECTION}」一节里"


def _pct(text: str) -> float:
    m = re.fullmatch(r"(-?[\d.]+)%", text)
    assert m, f"{text!r} 不是百分数字面量"
    return float(m.group(1))


def test_both_strategies_really_do_lose_to_buy_and_hold():
    """说明页写着"两个策略都大幅跑输躺平"。这是**推导出来的**结论，
    换测试池/换数据之后可能不再成立——那时这条断言会红，逼人改文案而不是留着假话。
    v0.4 口径（三策略 + 叠加层）跑输得更狠，一并验算。"""
    hold = _pct(guide.FACTS["hold_total"])
    for key in ("ma_total", "dc_total"):
        assert _pct(guide.FACTS[key]) < hold, \
            f"{key} 已经不再跑输等权买入持有（{guide.FACTS[key]} vs {guide.FACTS['hold_total']}）"
    v4_hold = _pct(guide.FACTS["v4_hold_total"])
    for key in ("v4_ma_total", "v4_dc_total", "v4_ts_total"):
        assert _pct(guide.FACTS[key]) < v4_hold, \
            f"{key} 已经不再跑输等权买入持有（{guide.FACTS[key]} vs {guide.FACTS['v4_hold_total']}）"


def test_the_drawdown_is_really_about_one_third():
    """同理验算"回撤只有它的约 1/3"。落在 1/5 ~ 1/2 之间才说得上"约 1/3"。
    v0.4 口径的三个回撤同样落在这个带内（0.35 ~ 0.43），文案沿用同一句话。"""
    hold = abs(_pct(guide.FACTS["hold_dd"]))
    for key in ("ma_dd", "dc_dd"):
        ratio = abs(_pct(guide.FACTS[key])) / hold
        assert 0.2 <= ratio <= 0.5, \
            f"{key} 的回撤是躺平的 {ratio:.0%}，「约 1/3」这句话该改了"
    v4_hold = abs(_pct(guide.FACTS["v4_hold_dd"]))
    for key in ("v4_ma_dd", "v4_dc_dd", "v4_ts_dd"):
        ratio = abs(_pct(guide.FACTS[key])) / v4_hold
        assert 0.2 <= ratio <= 0.5, \
            f"{key} 的回撤是躺平的 {ratio:.0%}，回撤对比的文案该改了"


def test_the_overlay_attribution_table_is_self_consistent():
    """归因表的核心结论是**推导**出来的，逐条验算，防止哪次重跑后文案变成假话：

    1. 双开 < 无叠加层（"叠加层在这个池子上是纯代价"）——三个策略都成立才许这么写；
    2. tsmom 的"只开止损"是全表最小值（"对慢信号是灾难"那段的数字支撑）；
    3. donchian 的"只开止损"≈ 无叠加层（差距 < 2 个百分点，"止损对它几乎无感"）。
    """
    for s in ("ma", "dc", "ts"):
        both, base = _pct(guide.FACTS[f"v4_{s}_total"]), _pct(guide.FACTS[f"v4_{s}_base"])
        assert both < base, \
            f"{s}: 双开（{both}%）不再低于无叠加层（{base}%），「纯代价」的文案该改了"
    all_cells = [_pct(guide.FACTS[f"v4_{s}_{c}"])
                 for s in ("ma", "dc", "ts") for c in ("base", "gate", "stop")]
    assert _pct(guide.FACTS["v4_ts_stop"]) == min(all_cells), \
        "tsmom 只开止损不再是全表最小，「对慢信号是灾难」的文案该改了"
    dc_gap = abs(_pct(guide.FACTS["v4_dc_stop"]) - _pct(guide.FACTS["v4_dc_base"]))
    assert dc_gap < 2.0, \
        f"donchian 开止损与否差了 {dc_gap:.1f} 个百分点，「几乎无感」的文案该改了"


def test_the_honest_conclusion_is_actually_written_down():
    """§3.1 第 4 条要求"如实写明实测结论"。最容易的偷懒是只列表格不下结论。"""
    body = guide.section("metrics").body
    assert "跑输" in body, "回测指标一节没写「跑输」这个结论"
    assert "1/3" in body, "没写「回撤只有约 1/3」这个另一面"
    assert "不是 bug" in body, "没说明这是诚实结果而不是缺陷"
    assert "蓝筹" in body and "趋势跟随" in body, "没解释为什么（长牛蓝筹对趋势跟随最不利）"


def test_scan_help_warns_that_same_sector_signals_are_one_bet():
    """§3.1 第 3 条点名要讲的陷阱：同板块多只信号本质是同一笔押注。
    机械规则完全不知道行业，用户全买就是把仓位押在一个 beta 上。"""
    body = guide.section("scan").body
    assert "板块" in body, "扫描一节没讲同板块信号是同一笔押注"
    assert "amount_ratio_20d" in body or "放量倍数" in body
    assert "假突破" in body, "没讲清放量倍数为什么比涨跌幅值得看"


def test_scan_section_ranks_volume_ratio_above_pct_chg():
    """§3.1 第 3 条的重点：amount_ratio_20d 比 pct_chg 更值得看。
    两列都提到了但没说清优先级，等于没讲。"""
    body = guide.section("scan").body
    assert "涨跌幅" in body and "放量倍数" in body
    assert "最低" in body or "低于" in body, \
        "没说明涨跌幅的参考价值低于放量倍数"


# ================================================================ 八个小节（§3.1）

EXPECTED_SECTIONS = ("tasks", "workflow", "scan", "metrics", "strategies",
                     "custom", "limits", "userdata", "safety", "cli")


def test_the_sections_of_the_spec_are_all_there_in_order():
    """§3.1 定的八节，顺序是设计过的（先讲是什么，再讲怎么读，最后安全与命令行）。
    v0.3.2 在「已知局限」与「安全提示」之间插入「你的数据在哪」——它讲的是
    "哪些文件是你的、不进 git、备份在哪"，与紧随其后的安全提示是同一类话题。
    v0.4.0 在「策略对比」之后插入「自定义策略」——先看懂内置的，再写自己的。"""
    assert tuple(s.key for s in guide.SECTIONS) == EXPECTED_SECTIONS


@pytest.mark.parametrize("key", EXPECTED_SECTIONS)
def test_every_section_has_a_title_and_a_body(key):
    sec = guide.section(key)
    assert sec.title.strip(), f"{key} 没有标题"
    assert len(sec.body.strip()) > 80, f"{key} 的正文太短，像是占位符: {sec.body!r}"


def test_section_lookup_rejects_an_unknown_key():
    """key 是测试与面板共用的定位手段，拼错必须响亮失败而不是静默返回 None
    （那会在面板上渲染出一片空白，谁都注意不到）。"""
    with pytest.raises(KeyError):
        guide.section("no-such-section")


def test_custom_section_has_the_two_steps_and_three_hard_rules():
    """「自定义策略」一节（v0.4.0 M4）：两步注册 + 三条硬规则，缺一条都等于没讲——
    这三条恰好都是"违反了不报错、只给假回测"的坑，文档是唯一的防线。"""
    body = guide.section("custom").body
    assert "REGISTRY" in body, "没讲注册那一步（REGISTRY 加一行）"
    assert "label" in body and "name" in body, "没讲内部键与显示名的分工"
    assert "adj_close" in body, "没讲「价格一律用后复权」这条硬规则"
    assert "shift(1)" in body, "没讲「前 N 日窗口自己 shift(1)」这条硬规则"
    assert "未来函数" in body, "没讲「只用当日及以前数据」这条硬规则"
    assert "死信号" in body, "没引用唐奇安全 0 死信号的教训"


def test_custom_section_code_template_actually_works(tmp_path):
    """模板代码不许烂：把正文里的 python 代码块存成临时模块**真实导入**，
    造一段行情验证它真能出仓位。文档里的示例代码没有测试就会腐烂成"照抄就报错"。"""
    import importlib.util

    import pandas as pd
    body = guide.section("custom").body
    m = re.search(r"```python\n(.*?)```", body, re.S)   # 第一个块就是完整模板（含 import）
    assert m, "自定义一节里没有策略类的 python 代码块"
    path = tmp_path / "my_break_template.py"
    path.write_text(m.group(1), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("my_break_template", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)               # 模板类定义必须原样可执行
    strat = mod.MyBreak(n=3)
    close = [10.0, 11.0, 12.0, 11.0, 13.0, 12.0]
    df = pd.DataFrame({"adj_close": close},
                      index=pd.bdate_range("2026-01-05", periods=len(close)))
    pos = strat.generate_positions(df)
    # 手算：前 3 日最高（不含当日），第 5 根 13 > max(11,12,11)=12 → 1，其余 0
    assert list(pos) == [0, 0, 0, 0, 1, 0]
    with pytest.raises(ValueError):
        mod.MyBreak(n=0)                       # 校验模板必须真的在校验


def test_the_task_section_draws_the_closed_loop():
    """§3.1 第 1 条要求配一张闭环图：扫描发现 → 加入 universe → 每日信号跟踪卖出。"""
    flow = guide.section("tasks").flow
    assert len(flow) >= 3, f"闭环图至少要三步，实际 {flow}"
    joined = " ".join(flow)
    assert "扫描" in joined and "universe" in joined and "信号" in joined, flow


def test_the_task_section_spells_out_who_watches_the_sell_side():
    """最容易踩的坑：扫描报了 BUY，人买了，票没进 universe，从此没人管卖出。"""
    body = guide.section("tasks").body
    assert "universe" in body
    assert "卖出" in body, "没讲清卖出信号只对 universe 里的票有效"


def test_the_workflow_section_says_to_wait_for_the_close():
    """收盘前跑必然失败（baostock 当日数据约 17:30 后才更新）。
    这是新用户第一次一定会撞的墙，工作流一节必须写在最前面。"""
    body = guide.section("workflow").body
    assert guide.FACTS["data_ready"] in body, "工作流没写「17:30 之后才有当日数据」"


def test_the_workflow_section_tells_how_long_things_take():
    """§3.2：空态与工作流都要给预计耗时，否则用户会以为面板卡死了。"""
    body = guide.section("workflow").body
    for key in ("scan_minutes", "backtest_seconds"):
        assert guide.FACTS[key] in body, f"工作流一节没给 {key} 的耗时"


def test_the_strategy_section_contrasts_both_built_ins():
    """§3.1 第 5 条：双均线（看相对位置、不看量、迟钝）vs 唐奇安（看突破、
    必须放量、反应快、出场急）。只列参数不讲性格就等于没写。"""
    body = guide.section("strategies").body
    for word in ("双均线", "唐奇安", "突破", "放量"):
        assert word in body, f"策略对比一节缺「{word}」"
    assert guide.FACTS["ma_trades"] in body and guide.FACTS["dc_trades"] in body, \
        "没用交易次数说明「一个迟钝一个急躁」"


def test_the_strategy_section_marks_the_evidence_strength():
    """v0.4.0 M3（设计 §3.2）：对比表要**标注证据强度**——时序动量 ★★★，
    双均线与唐奇安是经典教学样品。

    这不是排版讲究：三个策略并排列出来，用户默认会以为它们地位相同，
    于是把"教科书上有"当成"有效"。同一节还要写明为什么 MACD/KDJ/RSI 不做，
    否则"少了功能"与"故意不做"分不开。
    """
    body = guide.section("strategies").body
    assert "证据强度" in body, "策略对比表没有证据强度这一行"
    assert "★★★" in body, "没标出时序动量的证据强度最强"
    assert "教学样品" in body, "没说清双均线与唐奇安只是经典教学样品"
    for absent in ("MACD", "KDJ", "RSI"):
        assert absent in body, f"没交代为什么不做 {absent}"


def test_the_limits_section_lists_the_five_named_approximations():
    """§3.1 第 6 条点名的五项：涨跌停近似、除权按分红再投资、夏普 rf=0、
    沪深300 不含分红、扫描池仅主板非 ST。"""
    body = guide.section("limits").body
    for word in ("涨跌停", "分红再投资", "rf = 0", "沪深300", "主板"):
        assert word in body, f"已知局限一节缺「{word}」"


def test_the_safety_section_is_a_block_of_its_own():
    """§3.1 第 7 条：安全提示单独成块、醒目。emphasis=True 让面板用 st.warning 渲染。"""
    safety = guide.section("safety")
    assert safety.emphasis is True, "安全提示必须单独成块（面板据此走 st.warning）"
    assert [s.key for s in guide.SECTIONS if s.emphasis] == ["safety"], \
        "只有安全提示该醒目；到处都是警告框等于没有警告"


def test_the_safety_section_forbids_binding_to_all_interfaces():
    """面板能执行本机命令，暴露到局域网就等同于挂了个远程命令执行接口。
    这句话必须**逐字**带上那个开关，否则用户不知道该躲什么。"""
    body = guide.section("safety").body
    assert "0.0.0.0" in body, body
    assert "投资建议" in body, "免责声明也该在这块里"


def test_the_safety_section_tells_users_to_bind_the_loopback_explicitly():
    """v0.2.1 M3 实跑纠正的一处**假话**：原文案（和 README、v0.2.0 设计文档）都写着
    "streamlit run 默认只监听本机，保持默认即可"。实测不是——

        $ streamlit run app/dashboard.py --server.port 8531
        $ lsof -nP -iTCP:8531 -sTCP:LISTEN
        Python  79141 watashi  6u  IPv6 ...  TCP *:8531 (LISTEN)      ← 所有网卡

        $ streamlit run app/dashboard.py --server.port 8532 --server.address 127.0.0.1
        Python  80013 watashi  6u  IPv4 ...  TCP 127.0.0.1:8532 (LISTEN)

    面板是个本地命令执行入口，"默认安全"这句话错了就不是文案瑕疵而是安全问题。
    所以文案必须给出**可执行的那一条**（显式绑回环），且不得再声称默认值安全。
    """
    body = guide.section("safety").body
    assert guide.FACTS["bind_flag"] in body, \
        f"安全提示没给出显式绑回环的开关（{guide.FACTS['bind_flag']}）: {body}"
    assert "所有网卡" in body, "没说清不加这个开关时 streamlit 监听的是什么"
    for lie in ("默认只监听本机", "保持默认即可"):
        assert lie not in body, f"安全提示又写回了那句假话「{lie}」"


def test_no_user_facing_copy_claims_the_default_bind_is_safe():
    """同一句假话不许从别处复活。

    只查**渲染给用户看的字符串**（面板走 AST 取字面量、README 取正文行）：
    注释与"这里原先写着 X，实测是假话"这种勘误说明必须允许存在，
    否则下一个人不知道为什么要写得这么啰嗦，很容易"精简"回去。
    """
    literals = [n.value for n in ast.walk(
        ast.parse((ROOT / "app" / "dashboard.py").read_text(encoding="utf-8")))
        if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    for lie in ("默认只监听本机", "保持默认即可"):
        assert [s for s in literals if lie in s] == [], \
            f"面板文案里还留着那句假话「{lie}」"
        # README 里只允许出现在"原先写着……是假话"那句勘误里
        for line in README.splitlines():
            if lie in line:
                assert "假话" in line, f"README 里有一处没标注勘误的「{lie}」: {line}"


# ---------------------------------------------------------------- 你的数据在哪（v0.3.2 §5）
#
# 这一节是本次改动的**用户可见面**：两份用户数据被移出版本控制之后，用户必须知道
# 它们在哪、不受 git 保护、备份在哪、换机器怎么带走。不写清楚的后果很具体：
# 换电脑时只 clone 了仓库，以为"东西都在"，结果一笔交易记录都没有。

def test_the_userdata_section_names_every_file_that_belongs_to_the_user():
    """三个路径都要出现在正文里：日志、日志备份、本地信号池。
    少写一个，用户换机器时就会漏带一个。"""
    body = guide.section("userdata").body
    for path in ("journal/trades.csv", "journal/trades.csv.bak",
                 "config/universe.local.yaml"):
        assert path in body, f"「你的数据在哪」一节没提到 {path}"


def test_the_userdata_section_says_these_files_are_not_in_git():
    """"git 历史等于免费撤销"这条自 v0.3.2 起对这两份文件**不再成立**。
    用户必须知道，否则他会继续指望一个不存在的安全网。"""
    body = guide.section("userdata").body
    assert "版本控制" in body or "git" in body, body
    assert "备份" in body, "没说清撤销靠的是 .bak 备份（不再是 git 历史）"


def test_the_userdata_section_tells_you_how_to_move_to_a_new_machine():
    """§5（E）点名要有这一条：换机器时把哪几个文件拷过去。"""
    body = guide.section("userdata").body
    assert "新机器" in body or "换机器" in body, body
    assert "拷" in body or "复制" in body, body


def test_the_userdata_section_says_settings_yaml_is_still_project_config():
    """分离的另一半也要说：settings.yaml 仍然受版本控制（成本模型、策略参数是
    项目决策），里面的 universe 只是**种子**。不说的话，用户会以为改它有用。"""
    body = guide.section("userdata").body
    assert "config/settings.yaml" in body
    assert "种子" in body or "默认池子" in body, body


@pytest.mark.parametrize("lie", ["写进 config/settings.yaml",
                                 "写入 config/settings.yaml",
                                 "写进 `config/settings.yaml`",
                                 "写入 `config/settings.yaml`"])
def test_no_copy_still_claims_the_pool_is_written_to_settings_yaml(lie):
    """v0.3.2 起面板写的是本地覆盖文件。任何还说"改动写进 settings.yaml"的文案
    都是**假话**——照它做（比如手改 settings.yaml 的 universe 期待生效）不会有任何
    效果，也不会有任何报错。同 v0.2.1 那次"默认绑定是安全的"勘误一个套路：
    渲染给用户看的字符串里一处都不许留。

    只查字符串字面量（走 AST）与 README 正文，不查注释——注释里解释
    "以前写的是 settings.yaml，现在改了"是正当的。
    """
    hits = [s for s in _app_copy_literals() if lie in s]
    assert hits == [], f"面板文案里还有一处「{lie}」（已经不是事实）：{hits}"
    for line in README.splitlines():
        assert lie not in line, f"README 里还有一处「{lie}」：{line}"


def _app_copy_literals() -> list[str]:
    """app/ 里所有**渲染给用户看的**字符串字面量（docstring 除外）。

    docstring 要排除：注释与 docstring 里解释"v0.3.2 之前写的是 settings.yaml、
    现在改成本地文件"是正当的，那是给维护者看的。
    每个文件**只 parse 一次**——分两次 parse 的话 `id()` 分属两棵不同的树，
    docstring 一个都排除不掉，测试会变成"永远通过"。
    """
    out: list[str] = []
    for path in sorted((ROOT / "app").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = {
            id(n.body[0].value)
            for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.ClassDef))
            and n.body and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)
            and isinstance(n.body[0].value.value, str)}
        out += [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in docs]
    return out


def test_the_docstring_filter_actually_filters_something():
    """上一条的自检：docstring 排除逻辑一旦失效（例如两次 parse 导致 id 全不匹配），
    那条断言就会开始扫 docstring 里的勘误说明并**误红**；反过来，若它把所有字面量
    都当 docstring 排除掉，那条断言就变成永远通过。两个方向都要有个哨兵。"""
    literals = _app_copy_literals()
    assert any(guide.POOL_NOTE == s for s in literals), \
        "连 POOL_NOTE 都没扫到：字面量收集坏了，那条勘误断言已经是空跑"
    assert not any(s.startswith("信号池的读写编排") for s in literals), \
        "app/pool.py 的模块 docstring 没被排除：docstring 过滤失效了"


def test_the_readme_has_the_same_userdata_section():
    """README 是外部第一入口（也是新机器上唯一能看到的东西——面板还没起来的时候）。
    面板里写了而 README 没写，等于对着一个打不开面板的人说"看面板"。"""
    section = _readme_section("你的数据")
    for path in ("journal/trades.csv", "journal/trades.csv.bak",
                 "config/universe.local.yaml"):
        assert path in section, f"README 的「你的数据」一节没提到 {path}"
    assert "版本控制" in section, section
    assert "新机器" in section or "换机器" in section, section


def test_the_cli_section_lists_all_three_entry_scripts():
    """§3.1 第 8 条：三条命令，且说明与按钮完全等价。"""
    body = guide.section("cli").body
    for script in ("run_market_scan.py", "run_daily_signal.py", "run_backtest.py"):
        assert script in body, f"命令行一节缺 {script}"
    assert "等价" in body, "没说明命令行与按钮等价"


def test_the_cli_section_warns_that_terminal_jobs_dodge_the_mutex():
    """命令行起的任务面板看不见，两个 baostock 会话会互踢下线——这是真会踩的坑。"""
    body = guide.section("cli").body
    assert "互斥" in body and "baostock" in body, body


# ================================================================ 就地帮助（§3.2）

def test_every_job_has_a_popover_help():
    """三张任务卡片右上角的 `?`。JOBS 里加了新任务却忘了写帮助，这条会红。"""
    assert set(guide.JOB_HELP) == set(jobs.JOBS), \
        f"JOB_HELP 与 JOBS 不一致: {set(guide.JOB_HELP) ^ set(jobs.JOBS)}"


@pytest.mark.parametrize("name", sorted(jobs.JOBS))
def test_job_help_answers_all_four_questions(name):
    """§3.2：这个任务做什么、大概多久、产物在哪。参数含义由 PARAM_HELP 分别回答。"""
    text = guide.JOB_HELP[name]
    assert len(text.strip()) > 60, f"{name} 的帮助太短: {text!r}"
    assert "output/" in text, f"{name} 的帮助没说产物落在哪"
    assert "**多久**" in text, f"{name} 的帮助没说大概多久"
    assert "**做什么**" in text, f"{name} 的帮助没说这个任务做什么"


def test_every_param_of_every_job_has_a_tooltip():
    """参数控件的 help=。加了新参数忘了写 tooltip，这条会红——
    一个没有解释的 `--limit` 输入框，用户只能猜。"""
    expected = {(job.name, p.name) for job in jobs.JOBS.values() for p in job.params}
    assert set(guide.PARAM_HELP) == expected, \
        f"PARAM_HELP 与参数 schema 不一致: {set(guide.PARAM_HELP) ^ expected}"


@pytest.mark.parametrize("key", sorted(
    {(job.name, p.name) for job in jobs.JOBS.values() for p in job.params}))
def test_param_tooltip_says_what_blank_means(key):
    """四个控件的值都可以留空，而留空的语义各不相同（全量 / 最近交易日 / 全部策略）。
    不说清楚，用户第一次点开始就会误跑一次 0.5-2 小时的全量扫描。"""
    text = guide.PARAM_HELP[key]
    assert text.strip(), f"{key} 的 tooltip 是空的"
    if key[1] != "refresh":         # flag 型控件不勾就是不勾，没有"留空"一说
        assert "留空" in text, f"{key} 的 tooltip 没说留空是什么意思: {text!r}"


def test_limit_tooltip_states_the_real_ceiling_and_the_full_pool_size():
    """--limit 的上限由 jobs.LIMIT_MAX 定；文案写错就是骗人。
    同时要说清"留空 = 全量约 3010 只"，让人知道自己在点多大的活。"""
    text = guide.PARAM_HELP[("market_scan", "limit")]
    assert str(jobs.LIMIT_MAX) in text, f"没写真实上限 {jobs.LIMIT_MAX}: {text!r}"
    assert guide.FACTS["scan_pool"] in text, f"没写全量池子有多大: {text!r}"


def test_strategy_tooltip_lists_the_registered_strategies():
    """策略下拉的选项来自注册表；tooltip 里如果漏了某个策略名，
    用户会以为面板少了功能。"""
    text = guide.PARAM_HELP[("backtest", "strategy")]
    for name in jobs.JOBS["backtest"].params[0].choices:
        assert name in text, f"策略 tooltip 缺 {name}: {text!r}"


def test_strategy_copy_pairs_every_display_label_with_its_key():
    """v0.4.0 M1：guide 文案换显示名，但键不许消失——CLI 与 settings.yaml
    认的是键，只写中文名会让人在命令行里无从下手。策略对比一节与策略 tooltip
    两处都要 label 与键成对出现（注册表驱动：新策略漏更新文案这条会红）。"""
    from quant.strategy import REGISTRY, strategy_label

    body = guide.section("strategies").body
    tooltip = guide.PARAM_HELP[("backtest", "strategy")]
    for key in REGISTRY:
        label = strategy_label(key)
        assert label in body, f"策略对比一节缺显示名 {label}"
        assert key in body, f"策略对比一节缺内部键 {key}"
        assert label in tooltip, f"策略 tooltip 缺显示名 {label}"


def test_refresh_tooltip_warns_it_costs_a_full_redownload():
    """--refresh 会丢掉缓存重拉十年日线。不警告的话它看起来只是个无害的勾选框。"""
    text = guide.PARAM_HELP[("backtest", "refresh")]
    assert "缓存" in text and "重拉" in text, text


# 九张表：三张回测产物表 + 每日信号 + 信号池（v0.2.2 M3）
# + 交易日志的四张（v0.3.0 M3：日志本身 / 持仓 / 逐笔平仓 / 按来源分组）
TABLE_KINDS = ("trades", "skipped", "scan", "signal", "universe",
               "journal", "positions", "closings", "by_source")


@pytest.mark.parametrize("kind", TABLE_KINDS)
def test_every_table_has_a_one_line_column_hint(kind):
    """§3.2：每个数据表格上方一行灰字解释关键列。"""
    hint = guide.TABLE_HINTS[kind]
    assert 10 < len(hint) < 200, f"{kind} 的表格说明长度不合理: {hint!r}"


def test_table_hints_cover_exactly_the_known_tables():
    """双向相等：漏写说明会漏，多写了（表删了说明没删）也会漏。"""
    assert set(guide.TABLE_HINTS) == set(TABLE_KINDS)


def test_trades_hint_explains_the_empty_pnl_column():
    """盈亏只在平仓那一笔上有值，买入行是空的。不解释就会被当成"数据丢了"。"""
    hint = guide.TABLE_HINTS["trades"]
    assert "平仓" in hint and "空" in hint, hint


EMPTY_KINDS = ("backtest", "signal", "scan")


@pytest.mark.parametrize("kind", EMPTY_KINDS)
def test_empty_states_guide_instead_of_just_reporting(kind):
    """§3.2：空态文案要有指导性——按钮在哪、等价命令是什么、大概多久。
    「暂无数据」四个字什么也没告诉人。"""
    text = guide.EMPTY_STATES[kind]
    assert "开始" in text, f"{kind} 的空态没指路到「开始」按钮"
    assert "scripts/" in text, f"{kind} 的空态没给等价命令"
    assert "约" in text, f"{kind} 的空态没给预计耗时"


def test_empty_states_cover_exactly_the_three_products():
    assert set(guide.EMPTY_STATES) == set(EMPTY_KINDS)


# ================================================================ 纯净性

def test_guide_module_does_not_import_streamlit():
    """文案层是纯数据：import streamlit 就没法在这里逐条断言了（也会拖慢测试）。"""
    src = (ROOT / "app" / "guide.py").read_text(encoding="utf-8")
    assert "import streamlit" not in src, "guide.py 不该依赖 streamlit"


def test_guide_module_has_no_hardcoded_percentages_outside_facts():
    """正文里就地硬写百分数就绕过了 FACTS 的对账机制。
    百分数只许出现在 FACTS 的值里（正文用 {占位符} 取）。"""
    src = (ROOT / "app" / "guide.py").read_text(encoding="utf-8")
    facts_block = src.split("FACTS", 1)[1].split("\n}", 1)[0]
    outside = src.replace(facts_block, "")
    # 允许 0.1% 这类"口径说明"式的小数（滑点、无风险利率区间），它们不是实测结论
    strong = [m for m in re.findall(r"-?\d{2,}\.\d%", outside)]
    assert strong == [], f"正文里硬写了实测百分数（应走 FACTS）: {strong}"


# ================================================================ 闭环图（theme.flow）

def test_flow_renders_every_step_with_arrows_between():
    html = theme.flow(("扫描发现", "加入 universe", "每日信号跟踪卖出"))
    assert 'class="qd-flow"' in html
    assert html.count('class="qd-flow-step"') == 3
    assert html.count('class="qd-flow-arrow"') == 2, "三步之间应有两个箭头"


def test_flow_escapes_its_steps():
    assert "<b>" not in theme.flow(("<b>x</b>",))


def test_flow_of_one_step_has_no_arrow():
    assert "qd-flow-arrow" not in theme.flow(("只有一步",))


def test_flow_of_nothing_renders_nothing():
    """空步骤列表不能留一个空盒子把版面顶开。"""
    assert theme.flow(()) == ""


@pytest.mark.parametrize("cls", ["qd-flow", "qd-flow-step", "qd-flow-arrow"])
def test_flow_classes_have_css_rules(cls):
    """构造出来的类名在 CSS 里必须真有规则，否则闭环图就是一行裸文字。"""
    assert f".{cls} {{" in theme.CSS
