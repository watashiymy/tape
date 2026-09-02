"""扫描产物的范围记录与路径隔离（v0.2.4 设计 §2）。

**要修的事故**：产物文件名过去只有日期（`output/scan/<date>.csv`），不记录本次扫了
多少只。于是 `--limit N` 的试跑会**静默覆盖**同一天的全量扫描结果。2026-08-28 查证
本地 5 份扫描 CSV：3 份已被试跑覆盖（08-24 原本 86 条信号、08-27 原本全量），
而 `output/` 不在版本控制内——那两份全量结果永久丢失。

危害不止丢文件：面板显示"08-27 无信号"时，用户分不出这是全市场真没机会，还是一次
3 只票冒烟测试的残渣。与本项目一路在防的"不报错但结论错"同源。

两条防线，缺一不可：

1. **路径隔离**（`scan_csv_path`）：`--limit N` 落在 `<date>_limit{N}.csv`。
   全量结果在**文件系统层面**就不可能被试跑覆盖——不是靠"记得别在有全量结果的那天
   试跑"这种约定，是靠文件名。同一天不同规模的试跑也互不覆盖；相同规模重跑仍覆盖
   自己（那是同一件事重做，符合预期）。

2. **范围记录**（`ScanMeta` + 伴生 JSON）：每份产物记住自己扫了多少只。

**为什么 meta 单独成文件、而不是往 CSV 里塞一列**：
信号数为 0 时 CSV 只有表头，没有任何一行能承载"我扫了 3010 只"这个事实——
而"扫了 3010 只得 0 条信号"与"扫了 3 只得 0 条信号"正是本次要区分的核心。
往每一行加元信息列还会污染下游的 pandas 读取（列结构变了）。

落盘沿用 `cache.py` / `symbols.py` 的两条既有约定：原子写（tmp + `os.replace`）、
损坏文件**响亮**报错并带上路径与自愈办法。缺文件返回 None——那是老产物的正常状态
（"范围未知"），不是错误。
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

SCAN_DIR = Path("output") / "scan"       # 默认落点（相对仓库根）；调用方可注入
META_SUFFIX = ".meta.json"               # 与 BarCache 的 <symbol>.meta.json 同一套命名
_LIMIT_TAG = "_limit"

# is_full 刻意**不在**必需字段里：它是算出来的，见 ScanMeta.is_full。
_REQUIRED_KEYS = ("date", "scanned", "pool_total", "limit", "signals", "skipped",
                  "failed", "elapsed_s", "started_at")


def scan_csv_path(day: date, limit: int | None = None,
                  scan_dir: str | Path = SCAN_DIR) -> Path:
    """这一趟扫描该写到哪个 CSV。纯函数：只算路径，不碰文件系统。

    无 limit → `<date>.csv`（全量产物的名字一个字不变，面板/README/用户习惯都指着它）；
    有 limit → `<date>_limit{N}.csv`。
    """
    tag = f"{_LIMIT_TAG}{limit}" if limit else ""
    return Path(scan_dir) / f"{day}{tag}.csv"


def meta_path(csv_path: str | Path) -> Path:
    """伴生 meta 的路径。必须**由 CSV 路径推出来**，两者一一对应：
    试跑的 meta 若落到全量的 meta 上，等于换个文件继续覆盖。"""
    csv_path = Path(csv_path)
    return csv_path.with_name(csv_path.stem + META_SUFFIX)


@dataclass(frozen=True)
class ScanMeta:
    """一趟扫描的范围与结果概览（字段见设计 §2.2）。

    `date` 这个字段名与 `datetime.date` 同名：`from __future__ import annotations`
    下注解不求值，安全；键名跟着落盘的 JSON 走（那份文件是给人看的第一手证据）。
    """

    date: date
    scanned: int          # 实际扫描只数（--limit 截取之后）
    pool_total: int       # 扫描池总数（--limit 之前）
    limit: int | None     # 试跑时为 N，全量为 None
    signals: int
    skipped: dict[str, int]
    failed: int
    elapsed_s: float
    started_at: datetime

    @property
    def is_full(self) -> bool:
        """扫满了整个池子才叫全量。**算出来的，不读盘上那个布尔值**：
        JSON 是手工改得动的普通文本，信它就等于允许一份 3 只票的试跑自称全量。
        （落盘时仍写一份进去，那是给人看的。）

        注意它的语义是"这趟的**目标**是不是整个池子"，**不含**"每只都拿到了结论"
        ——那是 `judged` 的事。两者混为一谈会让一次失败 800 只的全量扫描降级显示成
        「试跑」，那同样是假话（它不是 --limit 试跑）。"""
        return self.scanned == self.pool_total

    @property
    def judged(self) -> int:
        """完成了判定的只数 = 尝试数 − 取数失败数（与 run_market_scan 那道就绪闸门同一口径）。

        `scanned` 是尝试数：run_market_scan.py 写 meta 时传的是 total，而同一个文件
        两行之外给就绪闸门传的正是 `total - len(failures)`。徽标与 tooltip 一律以
        这个数为准报"有结论的有多少"。算出来的属性，不进 schema（`failed` 早在
        `_REQUIRED_KEYS` 里，老产物照读不误）。"""
        return self.scanned - self.failed

    def to_json(self) -> dict:
        return {"date": self.date.isoformat(), "scanned": self.scanned,
                "pool_total": self.pool_total, "limit": self.limit,
                "is_full": self.is_full, "signals": self.signals,
                "skipped": dict(self.skipped), "failed": self.failed,
                "elapsed_s": round(float(self.elapsed_s), 1),
                "started_at": self.started_at.isoformat(timespec="seconds")}

    @classmethod
    def from_json(cls, data: dict) -> ScanMeta:
        """反序列化。缺字段一律抛（调用方包成"文件损坏"）——补默认值就是编造范围。"""
        missing = [k for k in _REQUIRED_KEYS if k not in data]
        if missing:
            raise ValueError(f"缺字段 {missing}")
        if not isinstance(data["skipped"], dict):
            raise ValueError(f"skipped 不是对象: {data['skipped']!r}")
        return cls(date=date.fromisoformat(data["date"]), scanned=int(data["scanned"]),
                   pool_total=int(data["pool_total"]),
                   limit=None if data["limit"] is None else int(data["limit"]),
                   signals=int(data["signals"]),
                   skipped={str(k): int(v) for k, v in data["skipped"].items()},
                   failed=int(data["failed"]), elapsed_s=float(data["elapsed_s"]),
                   started_at=datetime.fromisoformat(data["started_at"]))


def save_meta(meta: ScanMeta, csv_path: str | Path) -> Path:
    """把范围记录写到 CSV 旁边，返回 meta 路径。

    原子写（同 cache.py / symbols.py）：全量扫描要 0.5-2 小时，收尾时按 Ctrl-C
    不许留下半截 JSON——那会让下一次读盘撞上"文件损坏"，而它其实什么都没坏。
    临时名走 mkstemp 而非固定后缀：扫描脚本与面板可能同时在跑。
    """
    path = meta_path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(meta.to_json(), f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)   # 写失败不留垃圾（成功后已被 replace 走）
    return path


def load_meta(csv_path: str | Path) -> ScanMeta | None:
    """读回某份扫描产物的范围记录。**缺文件返回 None**，那是老产物的正常状态。

    None 与"损坏"必须分得开：前者是 v0.2.4 之前的产物（渲染成"范围未知"），
    后者是真出了问题，要响亮报错并带上路径 + 自愈办法（沿用 symbols.py 的口径）。
    把损坏静默降级成 None，就会把"文件坏了"显示成"范围未知"，那个坏文件能躺几个月
    没人发现——正是本项目一路在防的静默失败。
    """
    path = meta_path(csv_path)
    if not path.exists():
        return None
    try:
        return ScanMeta.from_json(json.loads(path.read_text(encoding="utf-8")))
    except Exception as e:
        raise RuntimeError(
            f"扫描范围文件损坏: {path}（{type(e).__name__}: {e}），"
            f"删除该文件后该次扫描会退回显示「范围未知」；重跑扫描可重新生成") from e


def _sort_key(csv_path: Path) -> tuple[str, bool, str]:
    """排序键：(日期, 是不是全量, 文件名)。`max()` 取的就是"最新一天的全量"。

    日期是第一顺位（昨天的全量不该压住今天的结果，标题上带着日期，用户看得见）；
    同一天内全量优先于试跑（用户关心的是真结果）。

    不能退回朴素的 `sorted(files, reverse=True)[0]`：ASCII 里 '_'(0x5F) > '.'(0x2E)，
    "2026-08-27_limit3.csv" 排在 "2026-08-27.csv" **前面**，那样面板永远显示试跑。

    认不出日期的文件名（不该出现，但 output/scan/ 是个用户放得进任何东西的目录）
    退回按整名排序 + 不算全量：与本函数出现之前的行为一致，不会凭空少显示什么。
    """
    stem = csv_path.stem
    day = stem[:10]
    try:
        date.fromisoformat(day)
    except ValueError:
        return (stem, False, stem)
    return (day, stem == day, stem)


def latest_scan(scan_dir: str | Path = SCAN_DIR) -> Path | None:
    """面板该显示哪份扫描产物：最新一天的，同一天优先全量。没有产物返回 None。"""
    scan_dir = Path(scan_dir)
    files = list(scan_dir.glob("*.csv")) if scan_dir.is_dir() else []
    return max(files, key=_sort_key) if files else None


def scan_day(csv_path: str | Path) -> str:
    """产物文件名里的日期部分（标题用）。试跑的 `_limit3` 后缀不属于日期，
    连着显示会变成"全市场扫描（2026-08-27_limit3）"这种给机器看的标题。"""
    return Path(csv_path).stem.split(_LIMIT_TAG)[0]


# 老产物（v0.2.4 之前）读不到 meta 时的说法。**不猜、不编**：无论显示"全量"还是
# "试跑"都可能是错的，而这两个词恰恰是用户拿来做决定的依据。
UNKNOWN_SCOPE = "范围未知"
UNKNOWN_TIP = ("该文件产生于记录扫描范围之前（v0.2.4），无法判断是全量还是试跑；"
               "重跑一次扫描即可得到确切范围")


def scope_badge(meta: ScanMeta | None) -> tuple[str, str]:
    """范围徽标：(文案, tooltip)。渲染层负责套 pill，这里只管说什么。

    三种说法对应设计 §2.3：`全量 N 只` / `试跑 N 只` / `范围未知`，
    v0.5.0 起任何一种都在**有失败时**把失败数摆到台面上。

    为什么必须说：`scanned` 是**尝试数**（run_market_scan.py 写 meta 时传的是 total），
    取数失败的票根本没进策略判定。一趟 3010 只里失败 800 只的扫描，旧文案照样写
    「全量 3010 只」、tooltip 照样写「扫满了全部 3010 只」，而页面上同时挂着
    「今日无新信号」——用户据此以为全市场今天没机会，实际上四分之一的票压根没看。
    这正是本项目一路在防的"不报错但结论错"。

    刻意**不动** `is_full` 的判据（仍是"这趟的目标是不是整个池子"），也刻意不设
    失败率阈值：阈值是凭空发明的常量，与本模块"不猜也不编"的口径相抵。
    有失败就如实报数，多少算多由看的人自己判断。
    """
    if meta is None:
        return UNKNOWN_SCOPE, UNKNOWN_TIP
    if meta.is_full:
        text = f"全量 {meta.scanned} 只"
        tip = f"这一趟扫满了扫描池全部 {meta.pool_total} 只标的"
    else:
        text = f"试跑 {meta.scanned} 只"
        tip = (f"这是一次 --limit {meta.limit} 的试跑，只扫了扫描池 {meta.pool_total} 只里的前 "
               f"{meta.scanned} 只；结论不代表全市场。全量结果另存一份，不会被它覆盖")
    if meta.failed:
        text += f"（{meta.failed} 只取数失败）"
        # 措辞刻意不写"真正有结论的是 N 只"：judged 里还含着因流动性/ST/历史不足
        # 被**主动筛掉**的那些（它们走完了判定链，只是没进策略），把它们说成
        # "有结论"是往另一个方向夸大。这里只说"走完判定"，并点明它含哪些。
        tip += (f"；其中 {meta.failed} 只取数失败、连判定都没走到。"
                f"走完判定的是 {meta.judged} 只（含被流动性/ST/历史不足筛掉的）"
                f"——失败的票不等于没信号。重跑一次可以补上，而且不会覆盖"
                f"比它更完整的那份（CSV 是整份重写、不是累加，所以更差的一趟会另存）")
    return text, tip
