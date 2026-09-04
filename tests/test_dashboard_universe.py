# tests/test_dashboard_universe.py — v0.2.2 M3「信号池」页与扫描表的 ＋ 按钮（§3.4）
#
# 这个功能会**改写驱动全部三个脚本的配置文件**，所以断言的重点不是"页面画出来了"，
# 而是三件事：
#   1. 写进 config/settings.yaml 的内容对，且**非 universe 部分逐字节不变**
#      （注释是有价值的；yaml.safe_dump 那条路会静默删光——测试拿真实配置做 fixture）；
#   2. 拦得住的必须拦住：池外标的、清空到 0 只、代码格式不对；
#   3. **离线不许崩页**：扫描池清单要联网拉（约 2-4 分钟、7000+ 条），
#      拉不到时只能降级为不可添加 + 明说原因；配置读不到时整页降级为只读提示。
#
# 每行按钮走 st.column_config.ButtonColumn（1.61 实测支持 on_click）。AppTest 没给
# 这种按钮公开的 .click()，所以 conftest.click_row_button 照前端格式塞 WidgetState
# 触发回调——这样"点 − 真的改了文件"是端到端验证的，不是只验了一个回调函数。
import importlib.util
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st
import yaml
from streamlit.testing.v1 import AppTest

from quant.config import load_settings
from tests.conftest import click_row_button, copy_app, goto_page

ROOT = Path(__file__).resolve().parent.parent
REAL_CONFIG = ROOT / "config" / "settings.yaml"
PAGE = "信号池"
SIGNALS_PAGE = "信号"
SCAN_HEADER = "date,symbol,name,strategy,close,pct_chg,amount,amount_ratio_20d\n"


def _load_pool():
    """单独加载 app/pool.py（信号池的读写与校验编排，不碰 streamlit 上下文）。

    刻意让 pool.py 不 import 任何 app 内部模块（ui/theme/guide），本函数才能这么
    直接地加载它——纯逻辑要能在没有 Streamlit 运行时的情况下单测。
    """
    spec = importlib.util.spec_from_file_location("qd_pool_probe",
                                                  ROOT / "app" / "pool.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["qd_pool_probe"] = mod
    spec.loader.exec_module(mod)
    return mod


pool = _load_pool()


@pytest.fixture(autouse=True)
def _clear_pool_cache():
    """扫描池清单走 @st.cache_data(ttl=6h)，而缓存是**进程级**的：
    不清的话上一个测试拉到的清单会被下一个测试拿去用（离线降级那条永远测不到）。"""
    st.cache_data.clear()
    yield
    st.cache_data.clear()


def _config(tmp_path: Path, universe=("600519", "000333", "601318")) -> Path:
    """在 tmp_path 里放一份**真实** config/settings.yaml（只换 universe）。

    用真配置而不是手写三行 YAML：注释、印花税分段、scan 段都在里面，
    "只改 universe 块"这件事才有东西可比对。
    """
    from quant.config_edit import replace_universe_block
    path = tmp_path / "config" / "settings.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        replace_universe_block(REAL_CONFIG.read_text(encoding="utf-8"), universe),
        encoding="utf-8")
    return path


def _outside_universe(text: str) -> tuple[str, str]:
    """配置切成（universe 块之前, universe 块之后）；与 test_config_edit 同一手法：
    不复用被测实现的定位逻辑，实现里定位错了这里照样能发现。"""
    return text[:text.index("universe:")], text[text.index("\nbenchmark:"):]


class FakeProvider:
    """BaostockProvider 的替身。真的会登录 baostock 并拉 11500 行（约 2-4 分钟），
    离线测试里一次都不许发生。calls 记调用次数，用来验证缓存真的生效。"""

    calls = 0
    pool_rows = [("600519", "贵州茅台"), ("000333", "美的集团"),
                 ("601318", "中国平安"), ("600036", "招商银行")]
    fail_with: Exception | None = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_trade_calendar(self, start, end):
        return [date(2026, 8, 26), date(2026, 8, 27)]

    def get_all_symbols(self, as_of):
        type(self).calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        return pd.DataFrame(self.pool_rows, columns=["symbol", "name"])


@pytest.fixture
def fake_provider(monkeypatch):
    """把 quant.data.baostock_provider.BaostockProvider 换成替身。

    AppTest 在**本进程**里 exec 面板脚本，sys.modules 是同一份，所以这个 patch
    对面板里 `from quant.data import baostock_provider` 之后的属性取值同样有效
    ——前提是面板在**函数体内**取该属性（模块级 from ... import 会绑死原类）。
    """
    from quant.data import baostock_provider
    FakeProvider.calls = 0
    FakeProvider.fail_with = None
    monkeypatch.setattr(baostock_provider, "BaostockProvider", FakeProvider)
    return FakeProvider


def _at(tmp_path: Path, page: str = PAGE) -> AppTest:
    return goto_page(
        AppTest.from_file(str(copy_app(tmp_path)), default_timeout=30).run(), page)


def _texts(at: AppTest) -> str:
    """页面上所有说给人看的文字（含 toast、错误、提示、灰字）拼成一块。"""
    parts = [e.value for e in (*at.main.caption, *at.main.info, *at.main.warning,
                               *at.main.error, *at.main.markdown)]
    parts += [t.value for t in at.get("toast")]
    return "\n".join(parts)


def _errors(at: AppTest) -> str:
    """**只**取留在页面上的错误（st.error）。

    失败提示不能只在 `_texts` 里出现就算过——`_texts` 把 toast 也算进去，
    而 toast 几秒后自己消失。写配置失败意味着"文件一个字节都没动"，那句话必须
    留在版面上让人看见并决定要不要重试（pool.show_flash 的分档就是为此存在）。
    变异实验（v0.2.2 M3）验证过：把 show_flash 的 st.error 改成 st.toast、或把
    on_remove 的 flash 等级从 "error" 写成 "ok"，只看 `_texts` 的断言全都照旧通过。
    """
    return "\n".join(e.value for e in at.main.error)


def _table(at: AppTest, index: int = 0) -> pd.DataFrame:
    """表格喂进去的数据。信号池表是纯 DataFrame，扫描表是 Styler（红绿只能走它）。"""
    value = at.get("dataframe")[index].value
    return getattr(value, "data", value)


# ================================================================ §3.3 池子读写（纯逻辑）

def test_current_reads_the_universe_from_the_config(tmp_path):
    path = _config(tmp_path, ("600519", "000333"))
    assert pool.current(path) == ("600519", "000333")


def test_add_appends_to_the_end_and_leaves_every_other_byte_alone(tmp_path):
    """追加到末尾（diff 只多一行），且非 universe 部分逐字节不变。"""
    path = _config(tmp_path, ("600519", "000333"))
    before = path.read_text(encoding="utf-8")

    msg = pool.add("601318", config_path=path,
                   allowed={"601318", "600036"})

    assert "601318" in msg, msg
    assert pool.current(path) == ("600519", "000333", "601318")
    assert _outside_universe(path.read_text(encoding="utf-8")) == _outside_universe(before)


def test_add_refuses_a_symbol_outside_the_scan_pool_without_touching_the_file(tmp_path):
    """池外标的（ST / 次新 / 非主板）会让回测口径失真而不报错，必须拦在写盘之前。"""
    path = _config(tmp_path, ("600519",))
    before = path.read_bytes()

    with pytest.raises(ValueError, match="扫描池"):
        pool.add("300750", config_path=path, allowed={"600519", "000333"})

    assert path.read_bytes() == before, "被拒绝的添加不许留下任何写入"


def test_add_is_idempotent_and_says_so(tmp_path):
    """面板上重复点 ＋ 不该报错，也不该把同一只写两遍。"""
    path = _config(tmp_path, ("600519", "000333"))
    before = path.read_bytes()

    msg = pool.add("600519", config_path=path, allowed={"600519"})

    assert "已在" in msg, msg
    assert path.read_bytes() == before, "已在池中时不该重写文件"


def test_remove_drops_the_symbol_and_keeps_the_rest_of_the_file(tmp_path):
    path = _config(tmp_path, ("600519", "000333", "601318"))
    before = path.read_text(encoding="utf-8")

    msg = pool.remove("000333", config_path=path)

    assert "000333" in msg, msg
    assert pool.current(path) == ("600519", "601318")
    assert _outside_universe(path.read_text(encoding="utf-8")) == _outside_universe(before)


def test_remove_refuses_to_empty_the_pool(tmp_path):
    """空 universe 会让两个入口报出误导性的错误（v0.1.1 修过一次），
    宁可这次操作失败也不能写出去。"""
    path = _config(tmp_path, ("600519",))
    before = path.read_bytes()

    with pytest.raises(ValueError, match="至少"):
        pool.remove("600519", config_path=path)

    assert path.read_bytes() == before


def test_remove_rejects_a_symbol_that_is_not_in_the_pool(tmp_path):
    """界面上看到的池子与文件里的已经不同步（多开了一个标签页）时必须响亮失败。"""
    path = _config(tmp_path, ("600519", "000333"))
    with pytest.raises(ValueError, match="不在信号池"):
        pool.remove("601318", config_path=path)


# ================================================================ §3.4 选项与表格（纯逻辑）

def test_options_are_code_plus_name_so_you_can_search_by_either():
    """用户多半记得住名字记不住代码；选项写成"代码 名称"，两种都能搜。"""
    df = pd.DataFrame([("600519", "贵州茅台"), ("000333", "美的集团")],
                      columns=["symbol", "name"])
    assert pool.options(df) == ["600519 贵州茅台", "000333 美的集团"]
    assert pool.symbol_of("600519 贵州茅台") == "600519"


def test_symbol_of_rejects_anything_that_is_not_a_six_digit_code():
    """选项串是自己造的，但 selectbox 的值将来可能来自别处；
    解析出来的东西必须过一遍代码校验，不能把 '茅台' 当代码写进配置。"""
    with pytest.raises(ValueError):
        pool.symbol_of("茅台")


def test_pool_table_shows_code_name_and_latest_close(tmp_path):
    """§3.4 A 的列：代码 / 名称 / 最新价 / 每行一个 − 按钮。

    名称与最新价都可能查不到（扫描 CSV 只记出信号的票、缓存可能还没拉过）。
    缺值必须是 fmt.MISSING（—）而不是 0、也不是字面量 "None"：
    浏览器实测（v0.2.2 M3）`NumberColumn` 把缺值画成 "None"，一张七行的池子表里
    六个 "None" —— 所以这一列在 pool_table 里就已经格式化成字符串了。
    """
    from quant.report import fmt

    cache = tmp_path / "cache"
    cache.mkdir()
    bars = pd.DataFrame({"close": [1520.5, 1600.0]},
                        index=pd.to_datetime(["2026-08-26", "2026-08-27"]))
    bars.to_parquet(cache / "600519.parquet")

    assert pool.latest_close("600519", cache) == pytest.approx(1600.0), "取的是最后一根"
    assert pd.isna(pool.latest_close("000333", cache)), "没缓存就是 NaN，不许编 0"

    table = pool.pool_table(("600519", "000333"), {"600519": "贵州茅台"}, cache)

    assert list(table["代码"]) == ["600519", "000333"]
    # 缺名显示 —（v0.2.3）：空白像"没加载出来"，— 才是明确的"暂无"，
    # 与右边「最新价」用同一个符号。详见 tests/test_dashboard_names.py。
    assert list(table["名称"]) == ["贵州茅台", fmt.MISSING]
    assert list(table["最新价"]) == ["1,600.00", fmt.MISSING]
    assert "None" not in list(table["最新价"])
    assert list(table[pool.ACTION_COLUMN]) == [pool.REMOVE_LABEL] * 2


def test_a_corrupt_cache_file_leaves_the_price_blank_instead_of_crashing(tmp_path):
    """缓存里的 parquet 坏了（取数被 Ctrl-C 打断过）：`BarCache.load` 会**响亮**抛
    RuntimeError（带 symbol 与路径——K 线页要靠那句话告诉人删哪个文件）。但信号池页
    只是想显示一个参考价，不该为此整页打不开；这一列留 — 就够了。

    这条 except 分支是**变异实验**（v0.2.2 M3）发现没人覆盖的：把 `return float("nan")`
    改成 `return 0.0` 时 30 个用例全绿——而 0 正是本项目明令禁止的那种假数据
    （看着像价格，其实是"没取过数"）。
    """
    from quant.report import fmt

    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "600519.parquet").write_bytes("这不是 parquet".encode())

    assert pd.isna(pool.latest_close("600519", cache)), "缓存坏了也不许编一个 0 出来"
    assert list(pool.pool_table(("600519",), {}, cache)["最新价"]) == [fmt.MISSING]


def test_scan_table_marks_symbols_that_are_already_in_the_pool():
    """§3.4 B：扫描结果每行一个 ＋；已在池中的显示禁用态文案。"""
    df = pd.DataFrame({"symbol": ["600519", "000333"], "name": ["贵州茅台", "美的集团"]})
    table = pool.scan_table(df, ("000333",))
    assert list(table[pool.ADD_COLUMN]) == [pool.ADD_LABEL, pool.IN_POOL_LABEL]


# ================================================================ 扫描池清单（缓存）
#
# v0.2.3 起清单先看本地那份（data/symbols.parquet，由扫描脚本落盘）：新鲜就直接用，
# 用户点「加载可选标的清单」不该再等 2-4 分钟。清单路径由调用方传进来
# （pool.py 刻意不 import ui），测试一律用 tmp_path——传真实路径的话，
# 跑过一次真扫描之后这些测试就会去读线上文件，联网那条分支再也测不到。

def _missing(tmp_path: Path) -> str:
    """一个不存在的清单路径 = "本地还没有清单"，走联网那条老路。"""
    return str(tmp_path / "data" / "symbols.parquet")


def test_the_scan_pool_is_cached_with_a_ttl():
    """7000+ 条、约 2-4 分钟一次。没有缓存的话每次交互（每次点按钮）都重拉一遍。"""
    assert hasattr(pool.scan_pool, "clear"), "扫描池清单没走 st.cache_data"
    assert pool.scan_pool._info.ttl == pool.POOL_TTL_S
    assert 0 < pool.POOL_TTL_S <= 24 * 3600, "TTL 不合理：太长会一直用过期清单"


def test_the_scan_pool_falls_back_to_the_previous_trading_day(fake_provider,
                                                             monkeypatch, tmp_path):
    """当日清单约 17:30 后才有（baostock）。盘中打开面板时最近交易日会抛 ValueError，
    退一个交易日重试，而不是让"添加"整天不可用。"""
    calls: list[date] = []

    def get_all_symbols(self, as_of):
        calls.append(as_of)
        if as_of == date(2026, 8, 27):
            raise ValueError("query_all_stock(2026-08-27) 返回空")
        return pd.DataFrame([("600519", "贵州茅台")], columns=["symbol", "name"])

    # 必须走 monkeypatch：fake_provider 是**类**，裸赋值会留到本次会话的后续测试里
    # （替身的行为被永久改掉，后面几条断言就在测别的东西——踩过一次）
    monkeypatch.setattr(fake_provider, "get_all_symbols", get_all_symbols)
    df, as_of = pool.scan_pool(_missing(tmp_path))

    assert calls == [date(2026, 8, 27), date(2026, 8, 26)], calls
    assert as_of == "2026-08-26", as_of
    assert list(df["symbol"]) == ["600519"]


def test_a_fresh_local_listing_is_used_instead_of_going_online(fake_provider, tmp_path):
    """本次改动的第二件事：本地清单新鲜（7 天内）就直接用，省掉那 2-4 分钟。"""
    from quant.data.symbols import save_symbols
    path = tmp_path / "data" / "symbols.parquet"
    save_symbols(pd.DataFrame([("600519", "贵州茅台"), ("000333", "美的集团")],
                              columns=["symbol", "name"]), date.today(), path)

    df, as_of = pool.scan_pool(str(path))

    assert fake_provider.calls == 0, "本地清单新鲜却仍然联网拉了 2-4 分钟"
    assert list(df["symbol"]) == ["600519", "000333"]
    assert as_of == date.today().isoformat()


def test_a_stale_local_listing_still_goes_online(fake_provider, tmp_path):
    """过期的清单不能一直用下去：新股上市/退市/改名会积累。"""
    from quant.data.symbols import save_symbols
    path = tmp_path / "data" / "symbols.parquet"
    stale = date.fromordinal(date.today().toordinal() - 8)
    save_symbols(pd.DataFrame([("600519", "贵州茅台")], columns=["symbol", "name"]),
                 stale, path)

    df, _as_of = pool.scan_pool(str(path))

    assert fake_provider.calls == 1
    assert list(df["symbol"]) == [s for s, _ in FakeProvider.pool_rows]


def test_no_local_listing_falls_back_to_the_network_exactly_like_before(fake_provider,
                                                                       tmp_path):
    """降级：没有清单文件（还没跑过扫描）时行为与从前完全一致。"""
    df, as_of = pool.scan_pool(_missing(tmp_path))

    assert fake_provider.calls == 1
    assert list(df["symbol"]) == [s for s, _ in FakeProvider.pool_rows]
    assert as_of == "2026-08-27"


# ================================================================ 信号池页（AppTest）

def test_the_universe_page_lists_the_pool_with_a_remove_button(tmp_path):
    path = _config(tmp_path, ("600519", "000333"))
    at = _at(tmp_path)

    assert not at.exception, at.exception
    table = _table(at)
    assert list(table["代码"]) == ["600519", "000333"]
    assert pool.ACTION_COLUMN in dict(at.get("dataframe")[0].proto.button_click_widgets), \
        "移除列没注册成按钮列（on_click 不会被调用）"
    assert str(len(pool.current(path))) in _texts(at), \
        "底部没说当前池子有多少只（§3.4 A）"
    assert "universe.local.yaml" in _texts(at), \
        "底部没说改动会写进 config/universe.local.yaml（v0.3.2 起写的是本地文件）"


def test_clicking_remove_rewrites_the_config_and_toasts(tmp_path):
    """端到端：点表格里那一行的 − → 文件真的少一只、其余字节不变、页面给 toast。"""
    path = _config(tmp_path, ("600519", "000333", "601318"))
    before = path.read_text(encoding="utf-8")
    at = _at(tmp_path)

    at = click_row_button(at, pool.ACTION_COLUMN, 1, pool.REMOVE_LABEL)

    assert not at.exception, at.exception
    assert load_settings(path).universe == ("600519", "601318")
    assert _outside_universe(path.read_text(encoding="utf-8")) == _outside_universe(before)
    toasts = [t.value for t in at.get("toast")]
    assert any("000333" in t for t in toasts), toasts
    assert list(_table(at)["代码"]) == ["600519", "601318"], "表格没刷新"


def test_clicking_remove_on_the_last_symbol_explains_instead_of_breaking(tmp_path):
    """只剩一只时点 − ：给明确提示，配置一个字节都不许动，页面不许崩。"""
    path = _config(tmp_path, ("600519",))
    before = path.read_bytes()
    at = _at(tmp_path)

    at = click_row_button(at, pool.ACTION_COLUMN, 0, pool.REMOVE_LABEL)

    assert not at.exception, at.exception
    assert path.read_bytes() == before
    assert "至少" in _errors(at), f"失败提示必须是留在页面上的 st.error：{_texts(at)}"


def test_a_stale_row_index_is_ignored_instead_of_crashing(tmp_path):
    """两个标签页同时开着时，点下去的行号可能已经不存在了（另一个页面刚移除了几只）。
    按错位的行号去改配置是最坏的结果，所以越界一律当没点过。"""
    path = _config(tmp_path, ("600519", "000333"))
    before = path.read_bytes()
    at = _at(tmp_path)

    at = click_row_button(at, pool.ACTION_COLUMN, 7, pool.REMOVE_LABEL)

    assert not at.exception, at.exception
    assert path.read_bytes() == before


def test_the_universe_page_survives_a_corrupt_cache_file(tmp_path):
    """同一件事在页面层再钉一遍：坏缓存只该让「最新价」空一格，不该让这页打不开
    ——而这页是唯一能把误加的票删掉的地方，它崩了就没有退路了。"""
    from quant.report import fmt

    _config(tmp_path, ("600519", "000333"))
    cache = tmp_path / "data" / "cache"
    cache.mkdir(parents=True)
    (cache / "600519.parquet").write_bytes("这不是 parquet".encode())
    at = _at(tmp_path)

    assert not at.exception, at.exception
    assert list(_table(at)["最新价"]) == [fmt.MISSING, fmt.MISSING]


def test_the_universe_page_survives_a_missing_config(tmp_path):
    """tmp 里没有 config/settings.yaml（也就是任何人第一次把面板复制到别处的情形）：
    整页降级为提示，不许 traceback。"""
    at = _at(tmp_path)
    assert not at.exception, at.exception
    assert at.main.error, "读不到配置时必须明说"
    assert "settings.yaml" in _texts(at)


def test_the_add_box_only_fetches_the_pool_when_asked(tmp_path, fake_provider):
    """默认不拉清单：那是一次约 2-4 分钟的联网请求，打开页面就卡住是不可接受的。
    点了「加载」才拉，之后 6 小时走缓存。"""
    _config(tmp_path, ("600519",))
    at = _at(tmp_path)

    assert fake_provider.calls == 0, "打开页面就联网拉了清单"
    assert at.button(key=pool.LOAD_BUTTON_KEY), "没有加载候选清单的按钮"

    at = at.button(key=pool.LOAD_BUTTON_KEY).click().run()

    assert not at.exception, at.exception
    assert fake_provider.calls == 1
    labels = at.selectbox(key=pool.PICK_KEY).options
    assert "000333 美的集团" in labels, labels
    assert "600519 贵州茅台" not in labels, "已在池中的标的不该再出现在候选里"


def test_the_add_box_uses_the_local_listing_instead_of_waiting_2_to_4_minutes(
        tmp_path, fake_provider):
    """v0.2.3：跑过扫描之后本地就有清单了，点「加载」应当**瞬时**出候选，不再联网。"""
    from quant.data.symbols import save_symbols
    _config(tmp_path, ("600519",))
    save_symbols(pd.DataFrame([("600519", "贵州茅台"), ("000333", "美的集团")],
                              columns=["symbol", "name"]),
                 date.today(), tmp_path / "data" / "symbols.parquet")
    at = _at(tmp_path)

    at = at.button(key=pool.LOAD_BUTTON_KEY).click().run()

    assert not at.exception, at.exception
    assert fake_provider.calls == 0, "本地已有新鲜清单却还是联网拉了"
    assert "000333 美的集团" in at.selectbox(key=pool.PICK_KEY).options


def test_a_corrupt_local_listing_is_named_instead_of_silently_refetching(tmp_path,
                                                                        fake_provider):
    """坏清单文件必须被点名（路径 + 怎么自愈），不能悄悄退回联网重拉——
    那样每次开面板都白等 2-4 分钟，而那个坏文件永远没人发现。页面照样不许崩。"""
    from quant.data.symbols import save_symbols
    _config(tmp_path, ("600519",))
    path = tmp_path / "data" / "symbols.parquet"
    save_symbols(pd.DataFrame([("600519", "贵州茅台")], columns=["symbol", "name"]),
                 date.today(), path)
    path.write_bytes(path.read_bytes()[:20])
    at = _at(tmp_path)

    at = at.button(key=pool.LOAD_BUTTON_KEY).click().run()

    assert not at.exception, at.exception
    assert "symbols.parquet" in _texts(at), _texts(at)
    assert fake_provider.calls == 0


def test_the_scan_pool_is_not_refetched_on_every_interaction(tmp_path, fake_provider):
    """§3.3：清单必须缓存。少了缓存，每点一次按钮就是一次 2-4 分钟的联网请求。"""
    _config(tmp_path, ("600519",))
    at = _at(tmp_path)
    at = at.button(key=pool.LOAD_BUTTON_KEY).click().run()
    assert fake_provider.calls == 1

    at.run()                      # 再重跑两轮（等价于用户随便点点别的东西）
    at.run()

    assert fake_provider.calls == 1, "扫描池清单被反复重拉"


def test_picking_a_symbol_and_adding_it_writes_the_config(tmp_path, fake_provider):
    path = _config(tmp_path, ("600519",))
    before = path.read_text(encoding="utf-8")
    at = _at(tmp_path)
    at = at.button(key=pool.LOAD_BUTTON_KEY).click().run()

    at.selectbox(key=pool.PICK_KEY).set_value("000333 美的集团")
    at = at.button(key=pool.ADD_BUTTON_KEY).click().run()

    assert not at.exception, at.exception
    assert load_settings(path).universe == ("600519", "000333")
    assert _outside_universe(path.read_text(encoding="utf-8")) == _outside_universe(before)
    assert any("000333" in t.value for t in at.get("toast")), \
        [t.value for t in at.get("toast")]
    assert list(_table(at)["代码"]) == ["600519", "000333"], "表格没刷新"


def test_the_page_degrades_to_read_only_when_the_pool_cannot_be_fetched(tmp_path,
                                                                       fake_provider):
    """离线降级（§3.4）：拉不到清单 → 不许崩页、不许给一个空下拉框假装能选，
    必须说清为什么不能添加；池子表格照旧显示（那部分是纯本地的）。"""
    _config(tmp_path, ("600519", "000333"))
    fake_provider.fail_with = OSError("baostock login failed: 网络不可达")
    at = _at(tmp_path)

    at = at.button(key=pool.LOAD_BUTTON_KEY).click().run()

    assert not at.exception, at.exception
    text = _texts(at)
    assert "网络不可达" in text, f"没把真实原因说出来: {text}"
    assert pool.PICK_KEY not in [s.key for s in at.selectbox], \
        "拉不到清单时不该渲染候选下拉框（一个空下拉框等于假装还能选）"
    assert list(_table(at)["代码"]) == ["600519", "000333"], "表格该照常显示"


# ================================================================ 信号页的 ＋（§3.4 B）

def _scan_csv(tmp_path: Path, rows: str) -> Path:
    path = tmp_path / "output" / "scan" / "2026-08-27.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SCAN_HEADER + rows, encoding="utf-8")
    return path


def test_the_scan_table_gets_a_plus_button_per_row(tmp_path):
    _config(tmp_path, ("600519",))
    _scan_csv(tmp_path,
              "2026-08-27,000333,美的集团,ma_cross,72.5,3.1,1200000000,2.4\n"
              "2026-08-27,600519,贵州茅台,ma_cross,1600.0,1.2,3400000000,1.8\n")
    at = _at(tmp_path, SIGNALS_PAGE)

    assert not at.exception, at.exception
    table = _table(at)
    assert list(table[pool.ADD_COLUMN]) == [pool.ADD_LABEL, pool.IN_POOL_LABEL], \
        "已在池中的行必须显示禁用态文案，不能给个能点的 ＋"


def test_clicking_plus_in_the_scan_table_adds_the_symbol(tmp_path):
    """最短路径：看到信号当场加进池子。扫描池成员资格由扫描 CSV 本身作证
    ——那份文件里的每一只都是 get_all_symbols 筛过的（主板、非 ST、上市满 400 天），
    所以这条路径**不需要联网**。"""
    path = _config(tmp_path, ("600519",))
    _scan_csv(tmp_path,
              "2026-08-27,000333,美的集团,ma_cross,72.5,3.1,1200000000,2.4\n")
    at = _at(tmp_path, SIGNALS_PAGE)

    at = click_row_button(at, pool.ADD_COLUMN, 0, pool.ADD_LABEL)

    assert not at.exception, at.exception
    assert load_settings(path).universe == ("600519", "000333")
    assert any("000333" in t.value for t in at.get("toast")), \
        [t.value for t in at.get("toast")]


def test_clicking_the_already_in_pool_cell_changes_nothing(tmp_path):
    path = _config(tmp_path, ("600519",))
    _scan_csv(tmp_path,
              "2026-08-27,600519,贵州茅台,ma_cross,1600.0,1.2,3400000000,1.8\n")
    before = path.read_bytes()
    at = _at(tmp_path, SIGNALS_PAGE)

    at = click_row_button(at, pool.ADD_COLUMN, 0, pool.IN_POOL_LABEL)

    assert not at.exception, at.exception
    assert path.read_bytes() == before
    assert "已在" in _texts(at), _texts(at)


def test_the_scan_table_still_renders_without_a_config(tmp_path):
    """没有 config/settings.yaml 时（"哪些已在池中"无从判断）扫描表必须照旧可读，
    只是不给 ＋ 列——信号页不该因为配置读不到就整页打不开。"""
    _scan_csv(tmp_path,
              "2026-08-27,000333,美的集团,ma_cross,72.5,3.1,1200000000,2.4\n")
    at = _at(tmp_path, SIGNALS_PAGE)

    assert not at.exception, at.exception
    table = _table(at)
    assert list(table["symbol"]) == ["000333"]
    assert pool.ADD_COLUMN not in table.columns


# ================================================ 写后复核回滚在界面上的样子（§3.2 + §3.4）
#
# write_universe 写完会立刻 load_settings 复核，不符就回滚原文件并抛 **RuntimeError**
# （M2 的设计决定：宁可这次操作失败，也不能留下一个坏配置）。那是三条写入路径共同的
# 失败出口，界面必须把它当"这次没改成"说出来——甩一屏 traceback 的话，用户既不知道
# 配置到底改没改，也不知道该不该重试。
#
# 复核只能靠 monkeypatch 触发（spec §5 就是这么规定这条路径的测法）：正常情况下写下去的
# 块必然能读回来，所以这是一条纯防御路径——而防御路径自己崩掉是最难查的一类故障。
# 注意要 patch `quant.config_edit.load_settings`：config_edit 是 `from ... import`
# 进来的，patch `quant.config.load_settings` 对它无效（而且会把读取路径一起弄坏，
# 那样测的就是"配置读不到"那条降级，不是这条）。

def _break_the_write_check(monkeypatch) -> None:
    from quant import config_edit

    def boom(path):
        raise yaml.YAMLError("写下去的配置读回来是坏的")

    monkeypatch.setattr(config_edit, "load_settings", boom)


def test_remove_reports_the_rollback_instead_of_crashing_the_page(tmp_path, monkeypatch):
    path = _config(tmp_path, ("600519", "000333"))
    before = path.read_bytes()
    at = _at(tmp_path)
    _break_the_write_check(monkeypatch)

    at = click_row_button(at, pool.ACTION_COLUMN, 1, pool.REMOVE_LABEL)

    assert not at.exception, at.exception
    assert path.read_bytes() == before, "复核失败必须回滚成原文件"
    assert "回滚" in _errors(at), f"失败提示必须是留在页面上的 st.error：{_texts(at)}"


def test_the_scan_plus_reports_the_rollback_instead_of_crashing_the_page(tmp_path,
                                                                        monkeypatch):
    path = _config(tmp_path, ("600519",))
    _scan_csv(tmp_path,
              "2026-08-27,000333,美的集团,ma_cross,72.5,3.1,1200000000,2.4\n")
    before = path.read_bytes()
    at = _at(tmp_path, SIGNALS_PAGE)
    _break_the_write_check(monkeypatch)

    at = click_row_button(at, pool.ADD_COLUMN, 0, pool.ADD_LABEL)

    assert not at.exception, at.exception
    assert path.read_bytes() == before
    assert "回滚" in _errors(at), f"失败提示必须是留在页面上的 st.error：{_texts(at)}"


def test_the_add_box_reports_the_rollback_instead_of_crashing_the_page(tmp_path,
                                                                      fake_provider,
                                                                      monkeypatch):
    path = _config(tmp_path, ("600519",))
    before = path.read_bytes()
    at = _at(tmp_path)
    at = at.button(key=pool.LOAD_BUTTON_KEY).click().run()
    at.selectbox(key=pool.PICK_KEY).set_value("000333 美的集团")
    _break_the_write_check(monkeypatch)

    at = at.button(key=pool.ADD_BUTTON_KEY).click().run()

    assert not at.exception, at.exception
    assert path.read_bytes() == before
    assert "回滚" in _errors(at), f"失败提示必须是留在页面上的 st.error：{_texts(at)}"


# ================================ 写的是本地文件，不是 settings.yaml（v0.3.2 §2.2）
#
# 这一组守的是本次改动的**全部要点**：用户改自己的池子，不该让一个受版本控制的
# 项目配置文件变脏（每改一次池子 git 就提示提交、而那份 diff 与代码无关）。
# 断言用 sha256 而不是"内容看起来没变"：种子块本来就长得和写入格式很像，
# 肉眼比对与 yaml 取值比对都可能把"改了又改回来"当成没改。

def _sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _local(path: Path) -> Path:
    from quant.config import local_universe_path
    return local_universe_path(path)


def test_removing_a_symbol_leaves_settings_yaml_byte_identical(tmp_path):
    """点 − 之后：池子少一只、settings.yaml 的 sha256 一模一样、本地文件里是新池子。"""
    path = _config(tmp_path, ("600519", "000333", "601318"))
    before = _sha256(path)
    at = _at(tmp_path)

    at = click_row_button(at, pool.ACTION_COLUMN, 1, pool.REMOVE_LABEL)

    assert not at.exception, at.exception
    assert _sha256(path) == before, "改池子却动了受版本控制的 config/settings.yaml"
    assert _local(path).exists(), "没有写本地覆盖文件"
    assert load_settings(path).universe == ("600519", "601318")


def test_adding_from_the_scan_table_leaves_settings_yaml_byte_identical(tmp_path):
    path = _config(tmp_path, ("600519",))
    _scan_csv(tmp_path,
              "2026-08-27,000333,美的集团,ma_cross,72.5,3.1,1200000000,2.4\n")
    before = _sha256(path)
    at = _at(tmp_path, SIGNALS_PAGE)

    at = click_row_button(at, pool.ADD_COLUMN, 0, pool.ADD_LABEL)

    assert not at.exception, at.exception
    assert _sha256(path) == before
    assert load_settings(path).universe == ("600519", "000333")
    assert "000333" in _local(path).read_text(encoding="utf-8")


def test_adding_from_the_search_box_leaves_settings_yaml_byte_identical(tmp_path,
                                                                       fake_provider):
    path = _config(tmp_path, ("600519",))
    before = _sha256(path)
    at = _at(tmp_path)
    at = at.button(key=pool.LOAD_BUTTON_KEY).click().run()

    at.selectbox(key=pool.PICK_KEY).set_value("000333 美的集团")
    at = at.button(key=pool.ADD_BUTTON_KEY).click().run()

    assert not at.exception, at.exception
    assert _sha256(path) == before
    assert load_settings(path).universe == ("600519", "000333")


def test_the_page_says_the_pool_comes_from_the_local_file(tmp_path):
    """来源必须写在脸上：两个文件都有 universe，看不出用的是哪个的话，
    用户会以为自己在跟踪 A 池子而脚本每天在跑 B 池子。"""
    path = _config(tmp_path, ("600519",))
    pool.add("000333", config_path=path, allowed={"000333"})   # 造出本地覆盖
    at = _at(tmp_path)

    assert not at.exception, at.exception
    text = _texts(at)
    assert "本地" in text and "universe.local.yaml" in text, text
    assert list(_table(at)["代码"]) == ["600519", "000333"]


def test_the_page_says_the_pool_is_the_default_when_there_is_no_local_file(tmp_path):
    """新克隆的样子：还没有本地文件 → 说清"这是默认池子"，别让人以为这就是他自己的。"""
    _config(tmp_path, ("600519", "000333"))
    at = _at(tmp_path)

    assert not at.exception, at.exception
    text = _texts(at)
    assert "默认" in text, text
    assert "settings.yaml" in text, text


def test_a_broken_local_file_degrades_the_page_and_names_it(tmp_path):
    """本地文件被手改坏 → 整页降级为一句明确的错误（连"当前池子"都无从显示），
    而且必须指名是**哪个**文件、怎么脱身。悄悄回退到默认池子是最坏的结果。"""
    path = _config(tmp_path, ("600519",))
    _local(path).write_text("universe: []\n", encoding="utf-8")
    at = _at(tmp_path)

    assert not at.exception, at.exception
    text = _errors(at)
    assert "universe.local.yaml" in text, f"没指名坏的是哪个文件：{text}"
    assert "删除" in text, f"没给出路（删掉它就回到默认池子）：{text}"
    # 不许"降级"成一张默认池子的表：那等于悄悄回退，用户会以为自己的池子还在。
    assert not at.get("dataframe"), \
        f"坏本地文件时还画了池子表格（画的必然是默认池子）：{list(_table(at)['代码'])}"


# ================================================================ README（外部第一入口）

README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_the_readme_documents_how_to_use_the_signal_pool():
    """§5 的验收清单里有这条：README 补「信号池」用法一节。

    README 是项目的外部第一入口，也是本项目"数字与说法只有一处"的那一处
    （tests/test_guide.py 拿它跟面板文案逐条对账）。这一节必须说清三件事：
    两条增删路径、写入规则的底线、离线时会发生什么。
    """
    assert "信号池" in README, "README 没提到信号池页"
    heading = [ln for ln in README.splitlines()
               if ln.startswith("###") and "信号池" in ln]
    assert heading, "README 没有「信号池」这一节的标题"
    for point in (
        "＋",                       # 扫描表每行的加入按钮
        "− 移除",                   # 信号池页每行的移除按钮
        guide_fact("pool_fetch"),   # 拉清单的代价（与说明页同一个数字）
        "至少留 1 只",              # 写入规则的底线
        "逐字节",                   # 只改 universe 块，其余原样
        "回滚",                     # 写后复核失败要回滚
    ):
        assert point in README, f"README 的信号池一节没交代「{point}」"


def guide_fact(key: str) -> str:
    """取 app/guide.py 的实测事实（README 与说明页共用同一个数字，不许各写一套）。"""
    import importlib.util as _il
    spec = _il.spec_from_file_location("qd_guide_pool", ROOT / "app" / "guide.py")
    mod = _il.module_from_spec(spec)
    sys.modules["qd_guide_pool"] = mod
    spec.loader.exec_module(mod)
    return mod.FACTS[key]


# ================================================================ 扫描表的策略筛选（v0.5.0）

def _multi(at, label: str):
    """按标签取 multiselect（AppTest 的 multiselect 按 label 找最省事）。"""
    return next(m for m in at.get("multiselect") if m.label == label)


def test_the_scan_table_offers_a_strategy_filter_when_there_is_more_than_one(tmp_path):
    """一次全量扫描能报上百条（实测 189），从里面挑票是收盘后最花时间的动作。
    只有一个策略时不出这个控件——那时它是纯噪声。"""
    _config(tmp_path, ("600519",))
    _scan_csv(tmp_path,
              "2026-08-27,000333,美的集团,ma_cross,72.5,3.1,1200000000,2.4\n"
              "2026-08-27,600036,招商银行,donchian,38.0,0.9,2200000000,1.6\n")
    at = _at(tmp_path, SIGNALS_PAGE)
    assert not at.exception, at.exception
    assert _multi(at, "按策略筛"), "多策略时应有筛选控件"

    one = tmp_path / "output" / "scan" / "2026-08-28.csv"
    one.write_text(SCAN_HEADER + "2026-08-28,000333,美的集团,ma_cross,72.5,3.1,1.2e9,2.4\n",
                   encoding="utf-8")
    at2 = _at(tmp_path, SIGNALS_PAGE)
    assert [m for m in at2.get("multiselect") if m.label == "按策略筛"] == [], \
        "只有一个策略时不该出这个控件"


def test_the_title_reports_how_many_rows_are_on_screen(tmp_path):
    """标题要说条数——控制台那行就绪状态早就在说「报了 N 条」，换到真正给你看
    信号的这一页反而没有。筛过之后要给两个数，才看得出自己筛掉了多少。"""
    _config(tmp_path, ("600519",))
    _scan_csv(tmp_path,
              "2026-08-27,000333,美的集团,ma_cross,72.5,3.1,1200000000,2.4\n"
              "2026-08-27,600036,招商银行,donchian,38.0,0.9,2200000000,1.6\n")
    at = _at(tmp_path, SIGNALS_PAGE)
    blob = " ".join(e.proto.body for e in at.get("html"))
    assert "报了 2 条" in blob, blob[:400]


def test_filtering_keeps_the_table_and_the_plus_buttons_on_the_same_rows(tmp_path):
    """**这条是这批改动里最危险的地方。**

    「＋ 加入」与「＋ 记一笔」都按**行号/行序**绑定（pool._clicked_symbol 拿
    session_state 里的 row 去索引传进去的 symbols；journal_ui.prefills 同样按
    这一轮的行序）。给表格喂筛后的、给按钮喂筛前的，点 ＋ 就会加错票、
    记一笔会预填错代码——而且不会有任何报错。
    """
    cfg = _config(tmp_path, ("600519",))
    _scan_csv(tmp_path,
              "2026-08-27,000333,美的集团,ma_cross,72.5,3.1,1200000000,2.4\n"
              "2026-08-27,600036,招商银行,donchian,38.0,0.9,2200000000,1.6\n"
              "2026-08-27,601318,中国平安,donchian,45.0,1.5,1800000000,2.1\n")
    at = _at(tmp_path, SIGNALS_PAGE)
    _multi(at, "按策略筛").select("donchian").run()
    assert not at.exception, at.exception

    table = _table(at)
    assert list(table["symbol"]) == ["600036", "601318"], \
        f"筛选没生效: {list(table['symbol'])}"
    assert "筛出 2 / 3 条" in " ".join(e.proto.body for e in at.get("html"))

    # 从**行为**上验（AppTest 不暴露 column_config 里那份 args）：点筛后第 0 行的 ＋，
    # 加进池子的必须是 600036（筛后第一行），不是 000333（筛前第一行）。
    at = click_row_button(at, pool.ADD_COLUMN, 0, pool.ADD_LABEL)
    assert not at.exception, at.exception

    got = load_settings(cfg).universe
    assert "600036" in got, f"点了筛后第一行的 ＋，加进去的却不是它：{got}"
    assert "000333" not in got, "加错票了——按钮拿的是筛选前的行序"
