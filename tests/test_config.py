import dataclasses
from datetime import date
from pathlib import Path

import pytest

from quant import config
from quant.config import load_settings

# 必须从 __file__ 推导仓库根：相对路径 "config/settings.yaml" 依赖 cwd，
# 从任何非仓库根目录跑 pytest（IDE、CI 的绝对路径调用）该用例必挂。
REAL_CONFIG = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


def test_load_settings(tmp_path):
    cfg = tmp_path / "s.yaml"
    cfg.write_text(
        """
universe: ["600519", "000333"]
benchmark: "000300"
backtest: {start: "2016-01-01", capital: 5000000}
costs:
  commission_rate: 0.00025
  commission_min: 5.0
  stamp_tax:
    - {until: "2023-08-27", rate: 0.001}
    - {from: "2023-08-28", rate: 0.0005}
  slippage: 0.001
strategies:
  ma_cross: {fast: 20, slow: 60}
""",
        encoding="utf-8",
    )
    s = load_settings(cfg)
    assert s.universe == ("600519", "000333")
    assert s.start == date(2016, 1, 1)
    assert s.capital == 5_000_000
    assert s.costs.commission_min == 5.0
    assert s.strategies["ma_cross"]["fast"] == 20


def test_stamp_rate_segments(tmp_path):
    cfg = tmp_path / "s.yaml"
    cfg.write_text(
        """
universe: ["600519"]
benchmark: "000300"
backtest: {start: "2016-01-01", capital: 1000000}
costs:
  commission_rate: 0.00025
  commission_min: 5.0
  stamp_tax:
    - {until: "2023-08-27", rate: 0.001}
    - {from: "2023-08-28", rate: 0.0005}
  slippage: 0.001
strategies: {}
""",
        encoding="utf-8",
    )
    c = load_settings(cfg).costs
    assert c.stamp_rate(date(2023, 8, 27)) == 0.001
    assert c.stamp_rate(date(2023, 8, 28)) == 0.0005
    assert c.stamp_rate(date(2016, 1, 4)) == 0.001
    assert c.stamp_rate(date(2026, 8, 14)) == 0.0005


def test_stamp_rate_three_segments_and_order_independent(tmp_path):
    """回归：印花税若再次调整，最自然的改法就是往列表里追加一段。
    "满足任一边界即返回"的写法会让带 frm 的第二段吞掉其后所有日期，
    静默返回旧税率——错误税率污染每一次回测且永不报错。"""
    body = """
universe: ["600519"]
benchmark: "000300"
backtest: {start: "2016-01-01", capital: 1000000}
costs:
  commission_rate: 0.00025
  commission_min: 5.0
  stamp_tax:
%s
  slippage: 0.001
strategies: {}
"""
    seg_a = '    - {until: "2023-08-27", rate: 0.001}'
    seg_b = '    - {from: "2023-08-28", until: "2026-12-31", rate: 0.0005}'
    seg_c = '    - {from: "2027-01-01", rate: 0.00025}'

    for name, segs in [("正序", [seg_a, seg_b, seg_c]), ("乱序", [seg_c, seg_a, seg_b])]:
        cfg = tmp_path / f"s_{name}.yaml"
        cfg.write_text(body % "\n".join(segs), encoding="utf-8")
        c = load_settings(cfg).costs
        assert c.stamp_rate(date(2016, 1, 4)) == 0.001, name
        assert c.stamp_rate(date(2024, 5, 6)) == 0.0005, name
        assert c.stamp_rate(date(2027, 6, 1)) == 0.00025, name


def test_stamp_rate_gap_raises_loudly(tmp_path):
    cfg = tmp_path / "gap.yaml"
    cfg.write_text("""
universe: ["600519"]
benchmark: "000300"
backtest: {start: "2016-01-01", capital: 1000000}
costs:
  commission_rate: 0.00025
  commission_min: 5.0
  stamp_tax:
    - {until: "2020-12-31", rate: 0.001}
    - {from: "2022-01-01", rate: 0.0005}
  slippage: 0.001
strategies: {}
""", encoding="utf-8")
    c = load_settings(cfg).costs
    with pytest.raises(ValueError, match="没有覆盖"):
        c.stamp_rate(date(2021, 6, 1))  # 落在缺口里必须报错，不能悄悄返回某个税率


_BODY = """
universe: %s
benchmark: "000300"
backtest: {start: "2016-01-01", capital: %s}
costs:
  commission_rate: 0.00025
  commission_min: 5.0
  stamp_tax:
    - {until: "2023-08-27", rate: 0.001}
    - {from: "2023-08-28", rate: 0.0005}
  slippage: 0.001
strategies: {}
"""


def test_empty_universe_raises(tmp_path):
    """回归：空 universe 旧代码放行——run_daily_signal 打印误导性的
    "全部标的数据均未更新"（0==0 恒真）退出，run_backtest 在 equal_weight_hold
    深处抛 No objects to concatenate。必须在加载配置时就报错。"""
    cfg = tmp_path / "s.yaml"
    cfg.write_text(_BODY % ("[]", "5000000"), encoding="utf-8")
    with pytest.raises(ValueError, match="universe 不能为空"):
        load_settings(cfg)


@pytest.mark.parametrize("capital", ["-5000000", "0"])
def test_nonpositive_capital_raises(tmp_path, capital):
    """回归：负本金旧代码"成功"跑完回测——产出全零 metrics + 上万行
    "资金不足"跳过记录，exit 0，看起来像策略从不交易。"""
    cfg = tmp_path / "s.yaml"
    cfg.write_text(_BODY % ('["600519"]', capital), encoding="utf-8")
    with pytest.raises(ValueError, match="capital"):
        load_settings(cfg)


def test_real_config_file():
    """两个 tmp_path 测试都自带 YAML，谁也管不到真正被脚本加载的那个文件。
    这里钉住 config/settings.yaml 本身，防止手改配置时打错字。"""
    s = load_settings(REAL_CONFIG)
    # 不再钉死"正好 10 只"：自 v0.2.2 起 universe 由面板增删（§3.3），
    # 数量本就随用户操作变化，钉住它只会让每次正常改池子都红一条。
    # 真正要守的是格式与"至少留 1 只"。
    assert len(s.universe) >= 1
    # 全部 6 位数字：YAML 1.1 会把不加引号的 000333 当八进制解析成 219，
    # str() 之后变成 "219" —— 一个静默错误的股票代码。
    assert all(len(c) == 6 and c.isdigit() for c in s.universe)
    assert s.capital == 5_000_000
    assert s.costs.commission_rate == 0.00025
    assert s.costs.commission_min == 5.0
    assert s.costs.slippage == 0.001
    assert s.costs.stamp_rate(date(2023, 8, 27)) == 0.001
    assert s.costs.stamp_rate(date(2023, 8, 28)) == 0.0005
    assert set(s.strategies) == {"ma_cross", "donchian"}
    # v0.4.0 M2：ATR 追踪止损默认**开启**（k=3 而非海龟经典的 2，理由见设计 §2.2：
    # 既有实测已证明"出场太急、一次正常回调就被甩下车"是唐奇安跑输的主因）。
    # 钉住它是因为这三个数字决定每一笔交易何时认输，手改配置打错字必须当场红。
    assert s.overlays.atr_stop.enabled is True
    assert s.overlays.atr_stop.n == 20
    assert s.overlays.atr_stop.k == 3.0
    assert s.scan.history_days == 400
    assert s.scan.min_avg_amount == 50_000_000
    assert s.scan.top_n == 20


# ---------- scan 段（v0.1.1 §3.2）----------

def test_scan_defaults_when_section_missing(tmp_path):
    """旧配置没有 scan: 段必须照常加载并给出设计默认值——
    否则升级后所有既有配置文件（含用户手上的副本）集体 KeyError。"""
    cfg = tmp_path / "s.yaml"
    cfg.write_text(_BODY % ('["600519"]', "5000000"), encoding="utf-8")  # _BODY 无 scan 段
    s = load_settings(cfg)
    assert s.scan.history_days == 400
    assert s.scan.min_avg_amount == 50_000_000
    assert s.scan.top_n == 20


def test_scan_section_parsed(tmp_path):
    """显式 scan 段逐项覆盖默认；未给的键仍取默认（部分覆盖是配置文件的常态）。"""
    cfg = tmp_path / "s.yaml"
    cfg.write_text(_BODY % ('["600519"]', "5000000")
                   + "scan: {history_days: 500, min_avg_amount: 80000000}\n",
                   encoding="utf-8")
    s = load_settings(cfg)
    assert s.scan.history_days == 500
    assert s.scan.min_avg_amount == 80_000_000
    assert s.scan.top_n == 20  # 未显式给出 → 默认


# ---------- overlays 段（v0.4.0 M2 设计 §2.3）----------
#
# 叠加层（ATR 追踪止损，M3 再加趋势过滤）对**全部策略**生效，因此它的参数和
# strategies 段一样属于"改了就会改变每一笔交易"的东西：解析必须严格，
# 错的写法要当场报错，不能等到回测跑完才发现规则不是自己想的那套。


def _cfg(tmp_path, extra: str = "") -> Path:
    cfg = tmp_path / "s.yaml"
    cfg.write_text(_BODY % ('["600519"]', "5000000") + extra, encoding="utf-8")
    return cfg


def test_overlays_default_to_all_disabled_when_the_section_is_missing(tmp_path):
    """无 overlays 段 = 全部禁用。

    向后兼容不是客气：v0.4.0 之前的全部结论（README 实测数字、既有回测产物、
    用户手上的 config 副本）都是无叠加层口径。缺省若变成"开启"，
    老配置一升级就悄悄换了一套交易规则。
    """
    s = load_settings(_cfg(tmp_path))
    assert s.overlays.atr_stop.enabled is False


def test_the_overlays_section_is_parsed(tmp_path):
    s = load_settings(_cfg(tmp_path, "overlays:\n  atr_stop: {enabled: true, n: 10, k: 2.5}\n"))
    assert s.overlays.atr_stop.enabled is True
    assert s.overlays.atr_stop.n == 10
    assert s.overlays.atr_stop.k == 2.5


def test_a_partial_overlay_entry_keeps_the_designed_defaults(tmp_path):
    """部分覆盖是配置文件的常态：只写 enabled 也该拿到设计默认 n=20, k=3.0。"""
    s = load_settings(_cfg(tmp_path, "overlays:\n  atr_stop: {enabled: true}\n"))
    assert (s.overlays.atr_stop.enabled, s.overlays.atr_stop.n, s.overlays.atr_stop.k) \
        == (True, 20, 3.0)


@pytest.mark.parametrize("entry, needle, why", [
    ("{enabled: true, n: 0}", "atr_stop.n", "rolling(0) 无警告地返回全 NaN → 止损永不触发"),
    ("{enabled: true, n: -5}", "atr_stop.n", "负窗口"),
    ("{enabled: true, n: 2.5}", "atr_stop.n", "浮点窗口迟到 rolling 才崩且报错误导"),
    ("{enabled: true, n: \"20\"}", "atr_stop.n", "字符串窗口"),
    ("{enabled: true, n: true}", "atr_stop.n",
     "YAML 的 true 是 bool，而 isinstance(True, int) 为真——会静默变成 rolling(1)"),
    ("{enabled: true, k: 0}", "atr_stop.k", "k=0 → 阈值就是 peak，任何回撤都止损"),
    ("{enabled: true, k: -3.0}", "atr_stop.k", "负 k → 阈值在 peak 之上，一入场就止损"),
    ("{enabled: true, k: \"3.0\"}", "atr_stop.k", "字符串 k 会在乘法处才崩"),
    ("{enabled: \"false\"}", "atr_stop.enabled",
     "字符串 \"false\" 是真值——本想关掉的叠加层会静默开着"),
    ("{enabled: 1}", "atr_stop.enabled", "1/0 不是 bool，语义靠猜"),
])
def test_bad_overlay_params_raise_and_name_the_parameter(tmp_path, entry, needle, why):
    """一律 raise ValueError（不用 assert，-O 会剥除），报错带参数名与实际值。"""
    with pytest.raises(ValueError, match=needle) as e:
        load_settings(_cfg(tmp_path, f"overlays:\n  atr_stop: {entry}\n"))
    assert "实际" in str(e.value), f"报错必须给出实际值（{why}）: {e.value}"


def test_an_unknown_overlay_name_raises_instead_of_being_ignored(tmp_path):
    """认不出的叠加层名必须报错。

    静默忽略的下场很具体：M3 才实现趋势过滤，用户（或未来的我）提前在 config 里
    写上 trend_filter，配置看着开着、代码里根本没这回事，回测结论与配置不符且零告警。
    """
    with pytest.raises(ValueError, match="trend_filter") as e:
        load_settings(_cfg(tmp_path, "overlays:\n  trend_filter: {enabled: true, n: 200}\n"))
    assert "atr_stop" in str(e.value), "报错要列出可用的叠加层名"


def test_overlay_params_are_validated_even_when_disabled(tmp_path):
    """关着也要校验：留在配置里的坏参数会在某天被"打开开关"的那个人踩到，
    而那时的报错离改动点已经很远了。"""
    with pytest.raises(ValueError, match="atr_stop.n"):
        load_settings(_cfg(tmp_path, "overlays:\n  atr_stop: {enabled: false, n: 0}\n"))


def test_the_overlay_dataclass_validates_on_construction():
    """校验落在 dataclass 自身，不只在 load_settings 里：测试与脚本会直接构造它。"""
    with pytest.raises(ValueError, match="atr_stop.k"):
        config.AtrStopCfg(enabled=True, n=20, k=0.0)
    ok = config.AtrStopCfg(enabled=True, n=20, k=3.0)
    assert (ok.n, ok.k) == (20, 3.0)


# ---------- 本地信号池覆盖（v0.3.2 §2.2）----------
#
# `config/settings.yaml` 的 universe 语义降级为**种子**（新克隆的起步池子，随代码
# 提交）；用户真正在跟踪的那批标的落在 `config/universe.local.yaml`（gitignore）。
# 这一组测试守的是同一件事的两面：
#   1. 有本地文件就必须**用它**（否则用户以为在跟踪 A 池子，脚本在跑 B 池子）；
#   2. 本地文件坏了/空了/写法不对，一律**响亮抛错并指名路径**，
#      **绝不静默回退到种子值**——那正是本项目一路在防的"不报错但结论错"。

def _seeded(tmp_path, seed='["600519", "000333"]') -> Path:
    """在 tmp_path/config/ 放一份带种子 universe 的 settings.yaml，返回它的路径。"""
    cfg = tmp_path / "config" / "settings.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(_BODY % (seed, "5000000"), encoding="utf-8")
    return cfg


def _local(cfg: Path, text: str) -> Path:
    path = config.local_universe_path(cfg)
    path.write_text(text, encoding="utf-8")
    return path


def test_local_universe_path_sits_next_to_settings_yaml(tmp_path):
    """路径由 settings.yaml 推导，不写死"config/"：脚本可以 --config 指到别处，
    那时本地覆盖也该跟着走（否则改了个副本却影响不了它）。"""
    cfg = _seeded(tmp_path)
    assert config.local_universe_path(cfg) == cfg.parent / "universe.local.yaml"
    assert config.local_universe_path(str(cfg)) == cfg.parent / "universe.local.yaml"


def test_without_a_local_file_the_seed_is_used(tmp_path):
    """新克隆的样子：没有本地文件 → 用 settings.yaml 里的种子，且来源看得出来。"""
    cfg = _seeded(tmp_path)
    assert load_settings(cfg).universe == ("600519", "000333")
    assert config.load_local_universe(cfg) is None
    assert config.universe_source(cfg) is None


def test_a_local_file_overrides_the_seed(tmp_path):
    """有本地文件就必须用它——种子只是"还没选过池子"时的起步值。"""
    cfg = _seeded(tmp_path)
    local = _local(cfg, 'universe: ["002241", "002837"]\n')

    s = load_settings(cfg)

    assert s.universe == ("002241", "002837")
    assert config.load_local_universe(cfg) == ("002241", "002837")
    assert config.universe_source(cfg) == local


def test_the_override_touches_nothing_but_the_universe(tmp_path):
    """只覆盖 universe：成本模型、策略参数、scan 段是**项目决策**，
    仍然只由受版本控制的 settings.yaml 说话。"""
    cfg = _seeded(tmp_path)
    before = load_settings(cfg)
    _local(cfg, 'universe: ["002241"]\n')

    assert load_settings(cfg) == dataclasses.replace(before, universe=("002241",))


@pytest.mark.parametrize("text, why", [
    ("universe: [\n", "YAML 语法坏了（手改到一半存盘）"),
    ("universe: []\n", "空列表"),
    ('universe: "002241"\n', "写成了字符串，不是列表"),
    ("universe: {a: 1}\n", "写成了字典"),
    ("pool: [\"002241\"]\n", "键名拼错，没有 universe"),
    ("", "文件是空的"),
])
def test_a_broken_local_file_raises_and_names_the_path(tmp_path, text, why):
    """**绝不静默回退到种子值**：那会让用户以为在跟踪本地池子，而系统在跑种子池子
    ——盯着 7 只自选股，实际每天扫的是 10 只白马，且永远不报错。

    错误信息必须做到两件事：指名是哪个文件，给出可执行的出路。
    """
    cfg = _seeded(tmp_path)
    local = _local(cfg, text)

    for call in (lambda: load_settings(cfg), lambda: config.load_local_universe(cfg),
                 lambda: config.universe_source(cfg)):
        with pytest.raises(ValueError) as ei:
            call()
        msg = str(ei.value)
        assert str(local) in msg, f"{why}：报错没指名路径：{msg}"
        assert "删除" in msg and "settings.yaml" in msg, \
            f"{why}：报错没给出路（删掉它就回到种子池子）：{msg}"


def test_a_broken_local_file_never_yields_the_seed(tmp_path):
    """把上一条反过来钉一遍：坏文件那条路径上，种子值**一个都不许**冒出来。
    变异实验（v0.3.2）：把抛错改成 `return seed`，只看"抛了 ValueError"的断言
    仍然会红，但只看"universe 非空"的断言会全绿——所以这里直接钉住取值。"""
    cfg = _seeded(tmp_path, seed='["600519"]')
    _local(cfg, "universe: []\n")

    with pytest.raises(ValueError):
        load_settings(cfg)
    # 真正的危险是"没抛错、拿到了 600519"。上一行已经保证抛错，这一行保证
    # 将来有人把它改成静默回退时，这个文件里至少有一条测试直指那个值。
    assert "600519" not in config.local_universe_path(cfg).read_text(encoding="utf-8")


def test_an_octal_looking_code_in_the_local_file_raises(tmp_path):
    """本地文件是可以手改的，于是 YAML 1.1 那个老坑又回来了：裸写的 `000333`
    被解析成八进制 219，`str()` 之后是 "219" —— 一个格式合法、看着像模像样、
    其实完全错误的代码。种子那边由 test_real_config_file 钉着 6 位数字，
    本地这边必须自己拦。"""
    cfg = _seeded(tmp_path)
    _local(cfg, "universe: [000333]\n")

    with pytest.raises(ValueError, match="219"):
        load_settings(cfg)


def test_duplicates_in_the_local_file_are_collapsed(tmp_path):
    """手改时复制粘贴出重复项是常事。去重按首次出现的位置（与面板写入同一套规则），
    不排序——排序会让每次改动的 diff 跳来跳去。"""
    cfg = _seeded(tmp_path)
    _local(cfg, 'universe: ["002241", "002837", "002241"]\n')

    assert load_settings(cfg).universe == ("002241", "002837")


def test_the_real_repo_config_loads_with_whatever_local_file_is_here():
    """本机那份（可能有、可能没有本地覆盖）必须都能加载：这条是"用户改了本地池子
    之后整套东西还跑得起来"的最低保证。"""
    s = load_settings(REAL_CONFIG)
    assert len(s.universe) >= 1
    assert all(len(c) == 6 and c.isdigit() for c in s.universe)
    source = config.universe_source(REAL_CONFIG)
    assert source is None or source == config.local_universe_path(REAL_CONFIG)
