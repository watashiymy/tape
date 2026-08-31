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

from dataclasses import dataclass
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
    scan_raw = raw.get("scan") or {}   # 无 scan: 段的旧配置走 ScanConfig 默认值
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
    )
