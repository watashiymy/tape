"""全市场扫描入口（v0.1.1 设计 §3.4）：收盘后一条命令，扫沪深主板非 ST，输出当日新 BUY。

用法：
    .venv/bin/python scripts/run_market_scan.py [--config config/settings.yaml]
                                                [--limit N] [--date YYYY-MM-DD]
                                                [--refresh-symbols]
baostock 约 17:30 后才有当日数据。耗时 = 固定开销 + 每票速率（2026-08-24 实测拆解）：
拉全市场清单（get_all_symbols，约 11500 行分页）固定约 2-4 分钟/次；逐票增量取数约
0.5-2 秒/只（网络往返主导，热/冷缓存同量级——缓存省的是不重拉 400 自然日窗口的历史，
省不掉每票一次联网往返）。全量约 3200 只单次预估 0.5-2 小时（50 只实测外推，未做全量
实跑）；中断重跑不会重拉已缓存的历史。

自 v0.2.3 起清单会落盘到 data/symbols.parquet，7 天内直接复用——那 2-4 分钟的固定
开销因此每周只付一次（`--refresh-symbols` 可强制重拉；它与回测脚本的 `--refresh`
是两回事，后者管的是行情缓存）。这份文件同时是面板「名称」列的离线来源。

v0.2.4 补上两道**与清单来源解耦**的就绪闸门（require_trading_day /
data_not_ready_reason）：基准日不是交易日、或扫过的标的全部没更新到基准日，
一律非零退出且不落 CSV。解耦是这次的重点——v0.2.3 之前"当日数据是否就绪"全靠
拉清单时的空表报错兜底，清单一复用，闸门就被整条绕过了。

同版还做了产物隔离：`--limit N` 的试跑写到 `<交易日>_limit{N}.csv`，收尾再写一份
伴生的 `<同名>.meta.json` 记下这趟的扫描范围（见 quant.signal.scan_meta 的模块说明——
那里有这次事故的原委：本地 5 份扫描 CSV 里 3 份被试跑静默覆盖）。
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quant.config import load_settings
from quant.data import symbols
from quant.data.baostock_provider import BaostockProvider
from quant.data.cache import BarCache
from quant.data.service import DataService
from quant.signal import baseday, scan_meta
from quant.signal.market_scan import classify_and_scan, sort_signals
from quant.strategy import build_strategies

SCAN_DIR = Path("output/scan")
CACHE_DIR = Path("data/cache")
SYMBOLS_PATH = symbols.SYMBOLS_PATH     # 全市场清单的落点（测试注入 tmp_path）
CSV_COLUMNS = ["date", "symbol", "name", "strategy", "close", "pct_chg",
               "amount", "amount_ratio_20d"]
SKIP_KEYS = ("stale", "insufficient_history", "is_st", "low_liquidity", "no_signal")
# 每多少只打一行进度。25 而不是 100：实测 0.305 秒/只，100 只 ≈ 30 秒一格
# （最大实测间隔 43 秒），而面板每 2 秒重画一次卡片——14 次刷新里 13 次看到的是
# 上一次的数字。25 只 ≈ 7.6 秒一格，全程约 120 行、日志从 4.3 KB 涨到约 13 KB，
# LOG_TAIL_LINES=30 的滚动框吃得下。
# 它同时是"就绪闸门前缀版"的判定点（见循环里那处）：约 10 秒就能判出数据未就绪。
PROGRESS_EVERY = 25


def require_strategies(strategy_cfg: dict[str, dict]) -> list:
    """构造策略，空表则退出（与 run_daily_signal 同一守卫，必须在联网**之前**）。

    strategies 段缺失/为空时 config 静默给 {}——不挡住，扫描会把 3200 只全抓一遍
    然后"成功"输出一张空表，且与"今日无新信号"的正常日输出逐字相同，永远发现不了。
    """
    strategies = build_strategies(strategy_cfg)
    if not strategies:
        sys.exit("配置里没有任何策略（settings.yaml 的 strategies 段缺失或为空），拒绝空跑")
    return strategies


def fetch_with_retry(service, symbol: str, start, end):
    """取数失败重试一次；仍失败把异常抛回调用方计数（单票失败不得中断全场扫描）。"""
    try:
        return service.get_bars(symbol, start, end)
    except Exception:
        return service.get_bars(symbol, start, end)


def resolve_expected(provider: BaostockProvider, date_arg: str | None) -> date:
    """基准交易日：--date 指定则用之；缺省取最近交易日（含今天）。
    日历逻辑在 quant.signal.baseday（与 run_daily_signal.py 共用一份）。"""
    if date_arg:
        return date.fromisoformat(date_arg)
    try:
        expected = baseday.latest_trading_day(provider)
    except baseday.CalendarError as e:
        sys.exit(str(e))
    if note := baseday.weekday_note(expected):
        print(note.replace("基准为", "扫描基准为"))
    return expected


def require_trading_day(provider: BaostockProvider, expected: date) -> None:
    """闸门一：基准日必须真是交易日，且必须查在**长跑之前**（v0.2.4）。

    `--date` 是裸的 date.fromisoformat，写个周六/春节照样解析成功。此前全靠
    `get_all_symbols(expected)` 在空结果时抛 ValueError 兜住，但那是**清单**路径上的
    副作用：v0.2.3 把清单落盘复用之后，缓存命中就根本不调用它，闸门被整条绕过——
    周六也能"扫描成功"并落一份空 CSV。所以这道闸门必须与清单来源**解耦**，
    自己去查一次日历（实现在 quant.signal.baseday，两个信号脚本共用）。

    位置很讲究：排在拉清单（2-4 分钟）与逐票取数（全量 0.5-2 小时）之前。
    不带 `--date` 时这是一次冗余查询（几百毫秒），换"闸门不依赖上游是怎么算出
    expected 的"，值。
    """
    try:
        baseday.require_trading_day(provider, expected)
    except baseday.NotTradingDay as e:
        sys.exit(str(e))


def data_not_ready_reason(expected: date, *, scanned: int, stale: int) -> str | None:
    """闸门二：这一趟扫描的结论到底可不可信？不可信就返回原因（调用方非零退出）。

    与 run_daily_signal.py 的成熟写法同一语义（那边是 `len(stale) == len(bars)`）：
    **全部落后 = 数据源还没更新，不是"今日无新信号"**。两者的终端输出逐字相同，
    而全量扫描要 0.5-2 小时，事后根本分不出刚才那趟有没有意义。

    scanned 只数**扣掉取数失败的**：失败的票没有结论，不该进分母。否则 3000 只里
    偶发 1 只失败，就能让 `stale == 扫描池` 这个朴素判据永远不成立。

    两种"没有结论"：
    - scanned == 0：一只都没完成判定（全部取数失败，断网/限流）；
    - stale == scanned：完成判定的**全部**停在基准日之前 = 数据未就绪。
    注意 stale > 0 是必要条件，扫描池为空的情形由 main 里更早、更准确的守卫处理，
    不会掉进这里被误报成"数据未就绪"。
    """
    if scanned == 0:
        return (f"全部标的取数失败，没有一只完成判定——本轮没有任何结论（不是"
                f"「{expected} 无新信号」）。未写 CSV，见上面的失败清单")
    if stale and stale == scanned:
        return (f"全部 {scanned} 只标的的数据都停在 {expected} 之前：这是「数据未就绪」，"
                f"不是「今日无新信号」（baostock 约 17:30 后才有当日数据）。"
                f"未写 CSV，稍后再试")
    return None


def print_failures(failures: list[tuple[str, str]]) -> None:
    """失败清单。两条出口都要打：就绪校验不通过时提前退出，也得让人看见怎么失败的。"""
    if not failures:
        return
    print("失败清单（重试一次后仍失败）：")
    for sym, err in failures:
        print(f"  {sym}: {err}")


def load_universe(provider: BaostockProvider, expected: date, *,
                  refresh: bool = False) -> pd.DataFrame:
    """全市场清单：本地那份还新鲜就直接用，否则联网拉一次并落盘。

    联网拉一次固定 2-4 分钟（约 11500 行分页），而清单变动很慢（新股上市/退市/改名），
    每天重拉是纯浪费。复用时**必须把话说出来**：不然用户只知道这次快了，
    不知道为什么，也就无从判断名字/候选清单是不是旧的。

    清单文件损坏时 load_symbols 会响亮抛 RuntimeError，这里刻意不接：
    接住退化成"当作没有缓存去重拉"的话，每轮白付 2-4 分钟，而那个坏文件
    可以在磁盘上躺几个月没人发现。

    **落盘的是完整清单**，调用方的 `--limit` 截取必须发生在这之后：
    一次 `--limit 30` 若把文件写成 30 只，面板的名称列与候选清单会跟着只剩 30 只，
    而且没有任何报错。
    """
    if not refresh:
        cached = symbols.load_symbols(SYMBOLS_PATH)
        if cached is not None:
            listing, as_of = cached
            if symbols.is_fresh(as_of, date.today()):
                print(f"复用本地清单（as_of={as_of}，{len(listing)} 只，{SYMBOLS_PATH}）；"
                      f"省下拉取全市场清单的 2-4 分钟，要强制重拉加 --refresh-symbols")
                return listing
            print(f"本地清单已过期（as_of={as_of}），重新拉取全市场清单…")
        else:
            print(f"本地无全市场清单（{SYMBOLS_PATH}），首次拉取全市场清单…")
    else:
        print("按 --refresh-symbols 重新拉取全市场清单…")
    listing = provider.get_all_symbols(expected)     # ValueError 交给调用方（非交易日等）
    symbols.save_symbols(listing, expected, SYMBOLS_PATH)
    print(f"已拉取全市场清单 {len(listing)} 只并保存到 {SYMBOLS_PATH}（as_of={expected}）")
    return listing


def main() -> None:
    # allow_abbrev=False 是**必须**的：argparse 默认认前缀缩写，而 `--refresh` 恰好是
    # `--refresh-symbols` 的唯一前缀。回测脚本有个 `--refresh`（刷行情缓存），
    # 手顺打到这里就会被静默解释成"重拉清单"——白等 2-4 分钟，还以为行情重拉了。
    ap = argparse.ArgumentParser(description="全市场每日 BUY 信号扫描（沪深主板非 ST）",
                                 allow_abbrev=False)
    ap.add_argument("--config", default="config/settings.yaml")
    ap.add_argument("--limit", type=int, default=None, help="只扫前 N 只（试跑用）")
    ap.add_argument("--date", default=None,
                    help="以指定交易日为基准扫描（YYYY-MM-DD；缺省用最近交易日）")
    ap.add_argument("--refresh-symbols", action="store_true",
                    help="强制重拉全市场清单（默认 7 天内复用本地 data/symbols.parquet）。"
                         "注意与回测脚本的 --refresh 不是一回事，后者刷的是行情缓存")
    args = ap.parse_args()

    settings = load_settings(args.config)
    strategies = require_strategies(settings.strategies)   # 守卫在联网之前
    scan_cfg = settings.scan
    t0 = time.monotonic()
    started_at = datetime.now()    # 墙上时钟，只为写进 meta（t0 是单调钟，算不出时刻）

    signals: list[dict] = []
    failures: list[tuple[str, str]] = []
    counts = dict.fromkeys(SKIP_KEYS, 0)
    signal_symbols = 0
    with BaostockProvider() as provider:
        expected = resolve_expected(provider, args.date)
        require_trading_day(provider, expected)        # 闸门一，在两段长跑之前
        try:
            universe = load_universe(provider, expected, refresh=args.refresh_symbols)
        except ValueError as e:
            # 当日 17:30 前清单未更新：提示退出（与 run_daily_signal 一致）。
            # 注意这条**不再**是"当日数据是否就绪"的闸门——清单缓存命中时它根本不会触发，
            # 那件事现在由 require_trading_day + data_not_ready_reason 两道负责。
            sys.exit(str(e))
        pool_total = len(universe)     # 截取**之前**的池子大小，写进 meta 供面板判"全量与否"
        if args.limit:
            # 截取只影响本轮扫描；清单已在 load_universe 里整份落盘（见那里的说明）
            universe = universe.head(args.limit)
        total = len(universe)
        if total == 0:
            # 0 只标的扫出 0 条信号是纯粹的假成功。单独一条守卫而不是并进就绪校验：
            # 病因（清单空了）与"数据没到位"完全是两回事，报错必须说对。
            sys.exit(f"扫描池为空（{SYMBOLS_PATH} 里 0 只标的），拒绝空跑——"
                     f"加 --refresh-symbols 重拉清单，或删掉该文件后重跑")
        print(f"基准日 {expected}，扫描池 {total} 只，策略: {[s.name for s in strategies]}，"
              f"流动性门槛 20日均额 ≥ {scan_cfg.min_avg_amount:,.0f} 元")
        start = expected - timedelta(days=scan_cfg.history_days)
        service = DataService(provider, BarCache(CACHE_DIR))
        # 进度行的耗时**另立原点**：t0 含拉全市场清单那 2–4 分钟（首次或过期时），
        # 把它摊进"每票速率"会让面板按 [100/3012] 外推出的剩余时间高得离谱
        # ——实测两趟冷清单的跑法在第 100 只处报 ~1小时56分 与 ~7小时30分，
        # 而真实剩余分别是 34 与 58 分钟（3.4x / 7.7x）。
        # 总耗时那几处（汇总行、meta 的 elapsed_s）继续用 t0：那要的是墙钟总账。
        t_loop = time.monotonic()
        for i, (sym, name) in enumerate(zip(universe["symbol"], universe["name"]), 1):
            try:
                df, _warns = fetch_with_retry(service, sym, start, expected)
                df.attrs["symbol"], df.attrs["name"] = sym, name
                sigs, skip = classify_and_scan(df, strategies, expected,
                                               scan_cfg.min_avg_amount,
                                               overlays=settings.overlays)
            except Exception as e:
                failures.append((sym, f"{type(e).__name__}: {e}"))
            else:
                if skip is not None:
                    counts[skip] += 1
                elif sigs:
                    signal_symbols += 1
                    signals.extend(sigs)
                else:
                    counts["no_signal"] += 1
            if i % PROGRESS_EVERY == 0 or i == total:
                print(f"[{i}/{total}] 信号 {len(signals)} 条，失败 {len(failures)} 只，"
                      f"耗时 {time.monotonic() - t_loop:.0f}s", flush=True)
                # **就绪闸门的前缀版**（第一个检查点判一次就够）：整趟闸门在循环之后，
                # 17:30 前起全量扫描要跑完 3012 只才被告知"数据未就绪"——白等 15~25
                # 分钟。判据与循环后那道**完全一致**（同一个函数、同一句原文、分母同样
                # 扣掉失败票），只是作用在前 PROGRESS_EVERY 只上，约 40 秒就有结论。
                if i == PROGRESS_EVERY:
                    early = data_not_ready_reason(expected, scanned=i - len(failures),
                                                  stale=counts["stale"])
                    if early:
                        print_failures(failures)
                        sys.exit(f"\n{early}\n（前 {i}/{total} 只即已判定，"
                                 f"没有跑完全量）")

    signals = sort_signals(signals)
    # 闸门二：先判这轮结论可不可信，再决定要不要打印「今日无新信号」、要不要落盘。
    # 位置是要害——落盘必须排在闸门之后：留下的那份空 CSV 会出现在面板「信号」页，
    # 与一次正常的无信号扫描看不出区别，还会覆盖掉同一天早先跑出来的真结果。
    reason = data_not_ready_reason(expected, scanned=total - len(failures),
                                   stale=counts["stale"])
    if reason:
        print_failures(failures)
        sys.exit(f"\n{reason}（扫描 {total} 只，失败 {len(failures)} 只，"
                 f"总耗时 {time.monotonic() - t0:.0f}s）")

    print(f"\n===== {expected} 全市场新 BUY 信号 =====")
    if not signals:
        print("今日无新信号")
    else:
        with pd.option_context("display.float_format", "{:,.2f}".format):
            print(pd.DataFrame(signals[: scan_cfg.top_n], columns=CSV_COLUMNS)
                  .to_string(index=False))
        if len(signals) > scan_cfg.top_n:
            print(f"（终端仅展示成交额前 {scan_cfg.top_n} 条，共 {len(signals)} 条，全量见 CSV）")

    print(f"\n汇总：扫描 {total} 只 → 信号 {len(signals)} 条（{signal_symbols} 只标的）；"
          f"跳过 stale={counts['stale']}"
          f" insufficient_history={counts['insufficient_history']}"
          f" is_st={counts['is_st']}"
          f" low_liquidity={counts['low_liquidity']}"
          f" no_signal={counts['no_signal']}；"
          f"失败 {len(failures)} 只；总耗时 {time.monotonic() - t0:.0f}s")
    print_failures(failures)

    # 产物路径带上试跑规模（v0.2.4）：`--limit N` 写 `<date>_limit{N}.csv`，
    # 全量结果在**文件系统层面**就不可能被一次冒烟测试覆盖。此前本地 5 份扫描 CSV
    # 里有 3 份就是这么没的（08-24 原本 86 条信号），而 output/ 不在版本控制内。
    SCAN_DIR.mkdir(parents=True, exist_ok=True)
    out = scan_meta.scan_csv_path(expected, args.limit, SCAN_DIR)
    meta = scan_meta.ScanMeta(
        date=expected, scanned=total, pool_total=pool_total, limit=args.limit,
        signals=len(signals), skipped=dict(counts), failed=len(failures),
        elapsed_s=time.monotonic() - t0, started_at=started_at)
    # **不许用更不完整的一趟覆盖更完整的那份**（v0.5.0）。v0.2.4 的路径隔离只按
    # `--limit` 隔，按"到底走完了多少只判定"没隔——而 v0.5.0 的徽标 tooltip 正在
    # 劝人"重跑一次即可补上"：第一趟部分失败（比如断网前扫完 2800 只）之后重跑，
    # 若第二趟更差（网络更糟，只走完 200 只）却不为零，就会**同名覆盖**掉那份好的，
    # 而 output/ 不在版本控制里、也没有 .bak。
    #
    # 处理方式是**两份都留**而不是拒绝落盘：这一趟确实跑完了，它的结果有权存在。
    # 换个不撞名的名字另存，并响亮说清谁更完整、想用新的该怎么做。
    existing = scan_meta.load_meta(out)          # 损坏就抛（沿用既有口径，不静默）
    if existing is not None and existing.judged > meta.judged:
        out = out.with_name(f"{out.stem}_judged{meta.judged}{out.suffix}")
        print(f"\n注意：同名产物 {scan_meta.scan_csv_path(expected, args.limit, SCAN_DIR)} "
              f"那趟走完判定 {existing.judged} 只，比本趟的 {meta.judged} 只更完整；"
              f"本次**没有覆盖**它，新结果另存为 {out.name}。"
              f"确定要用新的就把那两个文件删掉再重跑。")
    pd.DataFrame(signals, columns=CSV_COLUMNS).to_csv(out, index=False)  # 空结果也留表头
    # CSV 先写、meta 后写：中途挂掉留下的是"有 CSV 无 meta"，面板照常显示表格并标
    # 「范围未知」——这是老产物本来就有的降级路径。反过来（meta 先写）留下的是一份
    # 说着"扫了 3010 只"却没有结果的范围记录，那才叫误导。
    saved_meta = scan_meta.save_meta(meta, out)
    # 「已保存:」这个前缀是面板解析产物路径的唯一钩子（runner/progress.py 的 _SAVED），
    # meta 必须换个说法打印——否则面板会把那份 JSON 当 CSV 读，回一句"产物读取失败"。
    print(f"已保存: {out}")
    print(f"已记录扫描范围: {saved_meta}（{scan_meta.scope_badge(meta)[0]}）")


if __name__ == "__main__":
    main()
