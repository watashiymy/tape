"""配置加载：settings.yaml（+ 本地 universe 覆盖）→ 不可变数据类。

**两类东西住在两个文件里**（v0.3.2 §2）：

- `config/settings.yaml`：**项目配置**（benchmark / backtest / costs / strategies /
  scan）。它们是项目决策，改动值得留痕、值得被测试钉住，所以受版本控制。
  里面的 `universe` 语义降级为**种子**：新克隆的起步池子。
- `config/universe.local.yaml`：**用户状态**（当前在跟踪哪批标的）。随交易变化、
  暴露关注标的，因此 gitignore；存在时**覆盖**种子值。

合并规则只有一条：本地文件存在就用它的 universe，不存在就用种子。
坏文件（语法错 / 空 / 不是列表 / 代码格式不对）一律**抛错并指名路径**，
**绝不静默回退到种子值**——静默回退 = 用户盯着 A 池子而三个脚本在跑 B 池子，
且永远不报错，正是本项目一路在防的"不报错但结论错"。
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date, datetime
from pathlib import Path

import yaml

from quant.universe import normalize_universe

#: 本地覆盖文件名。与 settings.yaml **同目录**（不写死 "config/"）：三个脚本都能
#: `--config` 指到别处，那时本地覆盖必须跟着走，否则改了个副本却影响不了它。
LOCAL_UNIVERSE_NAME = "universe.local.yaml"


def _to_date(v) -> date:
    # datetime 必须先于 date 判断：isinstance(datetime_obj, date) 为 True，
    # 漏判会让 datetime 一路流到回测循环里才炸（date 与 datetime 无法比较）。
    if isinstance(v, datetime):
        return v.date()
    return v if isinstance(v, date) else date.fromisoformat(str(v))


@dataclass(frozen=True)
class StampTaxRule:
    rate: float
    until: date | None = None  # 含当日
    frm: date | None = None    # 含当日


@dataclass(frozen=True)
class Costs:
    commission_rate: float
    commission_min: float
    slippage: float
    stamp_tax: tuple[StampTaxRule, ...]

    def stamp_rate(self, d: date) -> float:
        """取 d 当日适用的印花税率。语义是"该日期是否落在本段区间内"（与，不是或）。

        不可写成"满足任一边界就返回"——那样一旦追加第三段（税率再次调整时的
        自然改法），带 frm 的那段会吞掉其后所有日期，静默返回旧税率，
        而错误税率会污染每一次回测且永不报错。当前写法与声明顺序无关。
        """
        for r in self.stamp_tax:
            if r.frm is not None and d < r.frm:
                continue        # 尚未生效
            if r.until is not None and d > r.until:
                continue        # 已经失效
            return r.rate
        raise ValueError(f"没有覆盖 {d} 的印花税规则")


def _positive_int(name: str, v) -> int:
    """窗口长度：必须是 ≥1 的真整数。

    `isinstance(True, int)` 为真，所以 bool 要单独挡掉——YAML 里 `n: true` 会静默
    变成 `rolling(1)`，一个"能跑、但完全不是你要的规则"的止损。
    浮点/字符串窗口若放行，要等到 `rolling()` 里才崩，报错离病因很远。
    """
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise ValueError(f"参数 {name} 必须是不小于 1 的整数，实际为 {v!r}")
    return v


def _positive_float(name: str, v) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
        raise ValueError(f"参数 {name} 必须是大于 0 的数，实际为 {v!r}")
    return float(v)


def _flag(name: str, v) -> bool:
    """开关必须是真 bool。

    字符串 `"false"` 与整数 1 都是真值：放行的下场是"配置里写着关，实际一直开着"，
    而回测照常完成、零告警。YAML 的 `true/false/yes/no` 本来就解析成 bool，
    写出别的形态就是写错了，当场说破。
    """
    if not isinstance(v, bool):
        raise ValueError(f"参数 {name} 必须是布尔值（true/false），实际为 {v!r}")
    return v


def _mapping(where: str, v) -> dict:
    """配置段必须是映射（YAML 的 `键: 值` 块）。**None 当空映射**（写了段没给内容
    与整段缺失同义），其余形态一律当场报错并指名段。

    不挡的下场不是静默，而是报错离病因太远：`atr_stop: 3.0`（把 k 直接写在叠加层名
    后面，很自然的手误）会在 `.get` 上抛 `AttributeError: 'float' object has no
    attribute 'get'`，`overlays: 5` 抛 `TypeError: 'int' object is not iterable`——
    用户得自己回去猜是哪一段写坏了。
    """
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ValueError(
            f"配置段 {where} 必须是映射（写成 `键: 值`），实际为 {type(v).__name__}: {v!r}")
    return v


def _reject_unknown(where: str, raw: dict, known: set[str]) -> None:
    """认不出的键**报错**，绝不忽略。

    `scan` 与 `overlays` 是配置里唯一两处 `.get(键, 默认值)` 的段——也就是唯一两处
    键名拼错不会 KeyError 的段。静默忽略的下场很具体：`enabled` 漏个 d 写成 `enable`
    就是"配置里写着开、实际一直关着"，`min_avg_amount` 少个 n 就是门槛悄悄回到默认值
    而扫描换出一批不同的票；两者都零告警、exit 0。

    键名用 repr 打出来：`enable` 与 `enabled` 只差一个字符，不加引号根本看不出差在哪。
    """
    unknown = [k for k in raw if k not in known]
    if unknown:
        raise ValueError(
            f"配置段 {where} 里有认不出的键 {', '.join(map(repr, unknown))}，"
            f"可用: {sorted(known)!r}")


@dataclass(frozen=True)
class AtrStopCfg:
    """ATR 追踪止损（v0.4.0 M2 设计 §2.2）。默认 n=20, k=3.0，但**默认关闭**。

    k 取 3 而非海龟经典的 2：既有实测已证明唐奇安"出场太急、一次正常回调就被甩
    下车"是它跑输的主因，蓝筹波动下 2×ATR 过紧。
    """
    enabled: bool = False
    n: int = 20
    k: float = 3.0

    def __post_init__(self) -> None:
        # 校验落在 dataclass 自身而不只在 load_settings 里：测试与脚本会直接构造它，
        # 那条路径若不校验，坏参数就只在 YAML 那一侧被挡住。
        _flag("atr_stop.enabled", self.enabled)
        _positive_int("atr_stop.n", self.n)
        _positive_float("atr_stop.k", self.k)


@dataclass(frozen=True)
class TrendFilterCfg:
    """200 日趋势过滤（v0.4.0 M3 设计 §3.1，Faber 风格）。默认 n=200，**默认关闭**。

    语义是"价格在长期均线下方时不持有多头"：`gate = adj_close > MA(n)`，
    最终仓位 = 基础信号 AND gate——既挡入场，也强制出场。
    用个股自身的 200 日线而不是指数（设计 §6：不引入指数对齐的复杂度）。
    """
    enabled: bool = False
    n: int = 200

    def __post_init__(self) -> None:
        # 与 AtrStopCfg 同一理由：测试与脚本会直接构造它，校验必须在 dataclass 自身。
        _flag("trend_filter.enabled", self.enabled)
        _positive_int("trend_filter.n", self.n)


@dataclass(frozen=True)
class OverlaysCfg:
    """叠加层总配置：对**全部策略**生效（v0.4.0）。

    默认全关。"配置无 overlays 段 = 全部禁用"是向后兼容契约：v0.4.0 之前的全部
    结论（README 的实测数字、既有回测产物、用户手上的 config 副本）都是无叠加层
    口径，缺省若变成开启，老配置一升级就换了一套交易规则且无人知晓。

    字段顺序 = **叠加顺序**（先 trend_filter 再 atr_stop，见 pipeline.target_positions
    的文档串）：先决定"这个环境能不能持有"，再管"持有之后何时认输"。
    """
    trend_filter: TrendFilterCfg = TrendFilterCfg()
    atr_stop: AtrStopCfg = AtrStopCfg()


def _load_overlays(raw: dict | None) -> OverlaysCfg:
    """解析 overlays 段。认不出的叠加层名**和**叠加层内部认不出的参数名都报错。

    静默忽略的下场很具体：设计 §6 明说本版不做的 `vol_target`（波动率目标仓位）
    若被无声吞掉，用户看着"开着"的配置、跑的是没有那层的规则，零告警。
    严格度不能只做一半——叠加层**里面**的参数名同样要认（`enable`/`kk` 这种手误
    比未知叠加层名更常见，后果也更重：它决定每一笔交易何时认输）。

    每加一个叠加层就要在这里加三行（取段、认键、构造），因此 known 集合一律从
    `fields()` 派生：漏改一处的下场是"配置里写着的那层被静默忽略"。
    """
    raw = _mapping("overlays", raw)
    _reject_unknown("overlays", raw, {f.name for f in fields(OverlaysCfg)})

    trend_raw = _mapping("overlays.trend_filter", raw.get("trend_filter"))
    _reject_unknown("overlays.trend_filter", trend_raw,
                    {f.name for f in fields(TrendFilterCfg)})
    td = TrendFilterCfg()               # 默认值只在 dataclass 声明处维护一份

    atr_raw = _mapping("overlays.atr_stop", raw.get("atr_stop"))
    _reject_unknown("overlays.atr_stop", atr_raw, {f.name for f in fields(AtrStopCfg)})
    d = AtrStopCfg()
    return OverlaysCfg(
        trend_filter=TrendFilterCfg(
            enabled=trend_raw.get("enabled", td.enabled),
            n=trend_raw.get("n", td.n),
        ),
        atr_stop=AtrStopCfg(
            enabled=atr_raw.get("enabled", d.enabled),
            n=atr_raw.get("n", d.n),
            k=atr_raw.get("k", d.k),
        ),
    )


@dataclass(frozen=True)
class ScanConfig:
    """全市场扫描（v0.1.1 §3.2）。默认值即设计值，旧配置无 scan: 段时全部生效。"""
    history_days: int = 400            # 拉取历史窗口（自然日），约 270 根 K 线 > MA60 两倍
    min_avg_amount: float = 50_000_000  # 20 日均成交额门槛（元）
    top_n: int = 20                    # 终端打印条数（CSV 存全量）


@dataclass(frozen=True)
class Settings:
    universe: tuple[str, ...]  # 用 tuple 而非 list：frozen 只挡重新赋值，挡不住 list 原地修改
    benchmark: str
    start: date
    capital: float
    costs: Costs
    strategies: dict[str, dict]
    # 带默认值（且必须放末位）：既容旧 YAML 无 scan: 段，也容测试/脚本里
    # 直接 Settings(...) 构造的既有调用点——缺省即设计默认。
    scan: ScanConfig = ScanConfig()
    #: 叠加层（v0.4.0）。缺省全关 = 与 v0.4.0 之前逐位一致的旧口径。
    overlays: OverlaysCfg = OverlaysCfg()


def local_universe_path(settings_path: str | Path) -> Path:
    """本地 universe 覆盖文件的位置（settings.yaml 的同目录邻居）。"""
    return Path(settings_path).parent / LOCAL_UNIVERSE_NAME


def load_local_universe(settings_path: str | Path) -> tuple[str, ...] | None:
    """本地覆盖池子；**文件不存在返回 None**（= 没覆盖，用种子）。

    返回 None 而不是空元组：空元组会和"用户把池子清空了"混成一件事，而后者是
    非法状态（universe 至少 1 只）。这里 None 的含义只有一个——还没有本地文件。

    文件存在但读不出一个合法池子时**抛 ValueError 并指名路径**，绝不回退到种子：
    回退的下场是用户以为在跟踪本地那 7 只、系统实际每天扫种子那 10 只，
    两边都"正常工作"，没有任何一处报错。错误信息必须给出可执行的出路。
    """
    path = local_universe_path(settings_path)
    if not path.exists():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or "universe" not in raw:
            raise ValueError(
                f"缺少顶层 universe: 键，实际读到 {type(raw).__name__}")
        items = raw["universe"]
        if not isinstance(items, list):
            raise ValueError(
                f"universe 必须是列表（[...]），实际是 {type(items).__name__}: {items!r}")
        # normalize_universe 顺带管三件事：6 位数字、去重、至少留 1 只。
        # **不预先 str 化**：裸写的 `000333` 被 YAML 1.1 当八进制读成 int 219，
        # str 化会把它伪装成一个格式合法、其实完全错误的代码 "219"；
        # 原样交给 validate_symbol，它会明说"必须是字符串"（也就是"请加引号"）。
        return normalize_universe(items)
    except Exception as e:
        raise ValueError(
            f"本地信号池文件用不了: {path}（{type(e).__name__}: {e}）。"
            f"这里不猜、也不悄悄回退到种子池子（那会让你盯着一批标的、"
            f"而三个脚本每天在跑另一批）。出路二选一：删除该文件即可回到 "
            f"settings.yaml 里的默认池子；或按 `universe: [\"600519\", \"000333\"]` "
            f"的写法把它改好（代码要带引号，否则 000333 会被读成 219）。"
        ) from e


def universe_source(settings_path: str | Path) -> Path | None:
    """当前 universe 来自哪个文件：本地覆盖文件的路径，或 None（= settings.yaml 的种子）。

    面板据此提示"当前池子来自本地文件"。刻意**不只看文件是否存在**而是真解析一遍：
    坏文件在这里抛的是与 `load_settings` 一模一样的那句话，两处不可能给出不同答案。
    """
    if load_local_universe(settings_path) is None:
        return None
    return local_universe_path(settings_path)


def load_settings(path: str | Path) -> Settings:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    rules = tuple(
        StampTaxRule(
            rate=float(item["rate"]),
            until=_to_date(item["until"]) if "until" in item else None,
            frm=_to_date(item["from"]) if "from" in item else None,
        )
        for item in raw["costs"]["stamp_tax"]
    )
    costs = Costs(
        commission_rate=float(raw["costs"]["commission_rate"]),
        commission_min=float(raw["costs"]["commission_min"]),
        slippage=float(raw["costs"]["slippage"]),
        stamp_tax=rules,
    )
    universe = load_local_universe(path)      # 本地覆盖优先；没有才用种子
    if universe is None:
        universe = tuple(str(s) for s in raw["universe"] or ())
    if not universe:
        # 放行的下场都是静默的：run_daily_signal 打印误导性的"全部标的数据均未更新"
        # （0==0 恒真）退出；run_backtest 在 equal_weight_hold 深处抛 No objects to concatenate
        raise ValueError("universe 不能为空")
    capital = float(raw["backtest"]["capital"])
    if capital <= 0:
        # 负本金能"成功"跑完回测：全零 metrics + 上万行"资金不足"，exit 0
        raise ValueError(f"capital 必须大于 0，实际为 {capital!r}")
    # 无 scan: 段的旧配置走 ScanConfig 默认值；段在但键名拼错则报错（同 overlays 口径）
    scan_raw = _mapping("scan", raw.get("scan"))
    _reject_unknown("scan", scan_raw, {f.name for f in fields(ScanConfig)})
    d = ScanConfig()                   # 默认值只在 dataclass 声明处维护一份
    scan = ScanConfig(
        history_days=int(scan_raw.get("history_days", d.history_days)),
        min_avg_amount=float(scan_raw.get("min_avg_amount", d.min_avg_amount)),
        top_n=int(scan_raw.get("top_n", d.top_n)),
    )
    return Settings(
        universe=universe,
        benchmark=str(raw["benchmark"]),
        start=_to_date(raw["backtest"]["start"]),
        capital=capital,
        costs=costs,
        strategies={k: dict(v) for k, v in (raw.get("strategies") or {}).items()},
        scan=scan,
        overlays=_load_overlays(raw.get("overlays")),
    )
