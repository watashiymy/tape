"""「信号池」页（v0.2.2 §3.4 A）：在面板上增删「信号跟踪」跟踪的固定池。

页面只负责组装，读写与校验全在 app/pool.py → src/quant/{universe,config_edit}.py。

两条降级路径（都是实际会发生的，一条也不许崩页）：
1. **配置读不到**（文件不在 / YAML 被改坏 / universe 为空）：整页只剩一句明确的
   错误——连"当前池子"都无从显示，更不能拿一张空表让人以为池子空了；
2. **拉不到扫描池清单**（离线、baostock 抽风、盘中清单未更新）：只有"添加"不可用
   （必须联网核对标的是否在扫描池内），池子表格与 − 移除照常——移除不依赖清单，
   把它一起禁掉只会让人在离线时连"把误加的票删掉"都做不到。
"""
from __future__ import annotations

import streamlit as st

import guide
import pool
import theme
import ui


def page_universe() -> None:
    ui.page_head("信号池")
    pool.show_flash()          # 上一轮按钮回调留下的 toast / 错误
    try:
        symbols = pool.current(ui.CONFIG_PATH)
        # 来源与池子在同一个 try 里取：两处各判一次的话，本地文件坏掉时会出现
        # "错误提示说读不到、下面又画着一张默认池子的表"这种自相矛盾的版面。
        source = pool.source(ui.CONFIG_PATH)
    except pool.CONFIG_ERRORS as e:
        # 路径要写出来：面板可能被复制到别处运行，"读不到"最常见的原因就是 cwd/位置不对。
        # e 自己会指名到底是哪个文件坏了（种子还是本地覆盖）并给出脱身办法，原样转述。
        st.error(f"读不到信号池配置 `{ui.CONFIG_PATH}`（{type(e).__name__}: {e}）。"
                 "请确认 config/settings.yaml 存在、且它或 config/universe.local.yaml "
                 "里的 universe 至少有 1 只标的（上面的原因里写着到底是哪个文件）；"
                 "本页在此期间不做任何修改。")
        return
    # 小标题一行说完三件事（2026-11-16 第二轮之前是表格上方两行、页面最底下一行灰字）：
    # 这张表是什么、改了什么时候生效 → 帮助圆点；还是默认池子 → 一个灰色小标签，话在悬停里。
    note = guide.pool_source_note(source)
    pill = theme.pill(guide.POOL_DEFAULT_PILL, "idle", tooltip=note) if note else ""
    st.html(theme.section(f"当前信号池（{len(symbols)} 只）", pill,
                          help=guide.TABLE_HINTS["universe"]))
    ui.data_table(pool.pool_table(symbols, ui.symbol_names(), ui.CACHE_DIR),
                  _pool_columns(symbols), "信号池是空的")
    _add_block(symbols)


def _pool_columns(symbols: tuple[str, ...]) -> dict:
    """信号池表的列配置。按钮列的 args 带上**这一轮**的行序：
    回调跑在下一轮脚本之前，那时页面上的 DataFrame 已经不存在了，只能靠它认行。"""
    return {
        "代码": st.column_config.TextColumn("代码", width="small"),
        "名称": st.column_config.TextColumn("名称", width="small"),
        # 文本列而不是 NumberColumn：后者把缺值画成字面量 "None"（浏览器实测），
        # 而这张表多数行本来就没有缓存。值已由 fmt.fmt_amount 格式化（缺值 = —）。
        "最新价": st.column_config.TextColumn(
            "最新价", alignment="right",
            help="本地已下载行情里最后一天的收盘价（未复权的原始价）。"
                 "还没有行情的标的显示 —。"),
        pool.ACTION_COLUMN: st.column_config.ButtonColumn(
            pool.ACTION_COLUMN, on_click=pool.on_remove,
            args=(symbols, ui.CONFIG_PATH), key=pool.REMOVE_CLICK_KEY,
            help="把这只标的移出信号池。至少要留 1 只；移错了在下面搜名字加回来即可。"),
    }


def _add_block(symbols: tuple[str, ...]) -> None:
    """搜索添加（§3.4 A）。候选清单可能要联网拉约 2–4 分钟，所以**点了才拉**：
    一打开这页就卡两分钟是不可接受的，而只想移除标的的人根本不需要这份清单。"""
    st.html(theme.section("添加标的"))
    if not st.session_state.get(pool.LOAD_FLAG):
        # 这一下可能要联网拉两到四分钟——说在按钮自己的悬停提示里（点之前光标一定先到它上面）。
        with st.container(horizontal=True, horizontal_alignment="right"):
            load = st.button("加载可选标的清单", key=pool.LOAD_BUTTON_KEY,
                             icon=":material/download:", help=guide.POOL_LOAD_HINT)
        if load:
            st.session_state[pool.LOAD_FLAG] = True
            st.rerun()          # 重跑一轮：这一轮的版面就只剩下拉框，按钮不再占位
        return
    try:
        # 路径传字符串：st.cache_data 按参数值分缓存，而 pool.py 不认识 ui 的路径
        listing, as_of = pool.scan_pool(str(ui.SYMBOLS_PATH))
    except pool.FETCH_ERRORS as e:
        # 如实报出类型与原因（多半是"网络不可达"或"当日清单未更新"），
        # 并说清哪些功能还能用——只说"失败了"等于让人去猜。
        st.warning(f"拉不到候选清单（{type(e).__name__}: {e}）。"
                   "「加入」需要这份清单核对标的是否在扫描范围内，暂不可用；"
                   "上面的表格与 − 移除照常可用。")
        with st.container(horizontal=True, horizontal_alignment="right"):
            retry = st.button("重试", key=pool.RETRY_BUTTON_KEY, icon=":material/refresh:")
        if retry:
            pool.scan_pool.clear()     # 失败不进缓存，但清一下更直白
            st.rerun()
        return
    choices = [o for o in pool.options(listing) if pool.symbol_of(o) not in set(symbols)]
    if not choices:
        st.write("候选清单里的标的都已在信号池中。")
        return
    # 清单是哪天的、还有几只可选：会变的事实，留在明面上。
    st.caption(guide.POOL_AS_OF.format(day=as_of, n=len(choices)))
    # 搜索框与按钮同一行、按钮在右端（操作按钮一律靠右），底边对齐。一个横向容器，不用按比例的
    # 分栏：1024 宽时按钮那一栏只有一百出头像素，「加入信号池」会折成两行、还会顶出搜索框的上沿
    # （独立审查量的）；这样写按钮永远是它自己的宽度，搜索框占剩下的地方。
    with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="bottom"):
        picked = st.selectbox(
            "搜索代码或名称", choices, index=None, key=pool.PICK_KEY,
            placeholder="输入代码或名称筛选，例如 600519 / 茅台", help=guide.POOL_PICK_HELP)
        add = st.button("加入信号池", key=pool.ADD_BUTTON_KEY, icon=":material/add:",
                        disabled=picked is None)
    if add:
        _do_add(picked, listing)                # 在那一行外面做：结果提示要占整行


def _do_add(option: str, listing) -> None:
    """把选中的候选写进配置。成功/失败都走 flash + 整页重跑：
    上面的池子表格必须跟着变，否则用户看着一张旧表怀疑到底加没加上。"""
    try:
        symbol = pool.symbol_of(option)
        pool.flash("ok", pool.add(symbol, config_path=ui.CONFIG_PATH,
                                  allowed=set(listing["symbol"])))
    except pool.WRITE_ERRORS as e:      # 含写后复核回滚的 RuntimeError（见 pool.py）
        pool.flash("error", f"加入失败：{e}")
    st.rerun()
