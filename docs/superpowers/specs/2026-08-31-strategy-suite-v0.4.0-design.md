# 策略套件 v0.4.0 设计文档

- 日期：2026-08-31
- 状态：待实施（用户已批准顺序：显示名 → ATR 止损 → 200 日过滤 → 时序动量 → 自定义指南）
- 前置：v0.3.2 已合并 main（1510 项离线测试全绿）；分支 `feat/v0.4-strategy-suite`

## 0. 背景与定位

用户四问的落地：① `ma_cross`/`donchian` 名字粗糙 ③ 想要更多经过时间检验的策略
④ 想知道怎么自定义。本版本的立场（已与用户对齐）：

- **不堆无效指标**。MACD/KDJ/单纯 RSI 这类教科书信号，单独使用扣除成本后没有稳定
  超额收益，不做。只加证据最强的三样：ATR 追踪止损、200 日趋势过滤、时序动量。
- **先补真缺口再加入场信号**：系统目前等权满仓、没有止损——出场与仓位比入场更影响
  盈亏。所以前两样是**叠加层（overlay）**而不是新策略：对所有策略生效。

## 1. M1：显示名与内部键分离

### 1.1 原则

`ma_cross`/`donchian` 已渗入用户历史数据（扫描 CSV 的 strategy 列、回测目录名、
journal 的 source 合法值），**内部键永不改**；加一层中文显示名给人看。

### 1.2 实现

- `Strategy` 基类加类属性 `label: str`；`MaCross.label = "双均线交叉"`、
  `Donchian.label = "唐奇安通道突破"`（行业通行译名，不自创）。
- `strategy/__init__.py` 提供 `strategy_label(key) -> str`：REGISTRY 命中返回 label，
  未知键**原样返回**（旧产物里的键永远能显示，绝不抛错）。
- `journal_ui.SOURCE_LABELS` 改为派生：`{k: f"{strategy_label(k)}信号" for k in REGISTRY}`
  + discretionary/other 两个固定项——新策略自动获得日志侧显示名。
- `journal/schema.py` 的 `SOURCES` 改为派生自 REGISTRY + ("discretionary", "other")，
  不再手写死（今天就欠了 tsmom 一个座位）。
- UI 触点全部换 label（数据文件照旧存键）：回测运行选择器（`format_func`）、
  扫描/信号表的 strategy 列（表内映射显示）、控制台策略下拉（显示 label 传键）、
  guide 文案。**导出的 CSV/Excel 保持键**——导出物是数据，不是界面。

### 1.3 测试

- 每个注册策略都有非空 label 且互不相同（注册表驱动，新策略漏写会红）
- `strategy_label` 对未知键原样返回（旧产物兼容）
- SOURCES/SOURCE_LABELS 随 REGISTRY 派生（往 REGISTRY 塞个假策略断言两者自动跟上）
- AppTest：选择器显示中文、底层值仍是键

## 2. M2：信号流水线收拢 + ATR 追踪止损

### 2.1 先收拢，再叠加（本版本最重要的架构决策）

现状：`run_backtest.py:116`、`signal/market_scan.py:46`、`signal/scan.py:13`
**三处各自**调 `strat.generate_positions(df)`。叠加层若分别接入，任何一处漏接 =
"扫描说买、回测按另一套规则算、每日信号又是第三套"——静默分叉，正是本项目一路在防的。

新增 `src/quant/strategy/pipeline.py`：

```python
def target_positions(df: pd.DataFrame, strategy: Strategy,
                     overlays: OverlaysCfg) -> pd.Series:
    """唯一的目标仓位出口：base = strategy.generate_positions(df)，
    依序套 trend_filter（先）与 atr_stop（后），返回最终 0/1 序列。
    三个入口（回测/扫描/每日信号）一律经由此函数，不许再直调 generate_positions。"""
```

三个调用点全部改走它。**测试须钉住"不许直调"**：源码级断言
scripts/ 与 signal/ 下不再出现 `generate_positions(`（pipeline.py 与策略自身除外）。

### 2.2 ATR 追踪止损（overlay，对所有策略生效）

语义（海龟法则变体，全部用后复权价）：

- 持有期间维护 `peak = 入场以来最高 adj_close`；
- 当日 `adj_close < peak − k × ATR(n)` → 目标仓位归 0（引擎既有的 shift 机制
  使其 T+1 开盘卖出，与所有既有信号同一时序，无未来函数）；
- **止损后保持空仓，直到基础策略出现新的 0→1 入场**（基础信号一直是 1 不算：
  否则止损次日立即回补，止损形同虚设）。这条必须有专测。
- ATR 暖机期（前 n 根 NaN）不触发止损——算不出来就不装算得出来。

默认参数 `n=20, k=3.0`。k 取 3 而非海龟经典的 2：既有实测已证明唐奇安"出场太急、
一次正常回调就被甩下车"是它跑输的主因，蓝筹波动下 2×ATR 过紧；参数可配，
文档写明取舍。

### 2.3 配置

```yaml
overlays:                        # 叠加层：对全部策略生效（v0.4.0）
  trend_filter: {enabled: true, n: 200}
  atr_stop:     {enabled: true, n: 20, k: 3.0}
```

- `load_settings` 解析 + 校验（n≥1 整数、k>0，沿用既有 raise 纪律）；
  **配置无 overlays 段 = 全部禁用**（向后兼容既有测试与旧 config 快照）。
- `config_snapshot.json` 带上 overlays——否则回测产物无法解释自己是在什么规则下跑的。

### 2.4 测试（本里程碑主要产出，手算对照）

- 手造序列：入场→连涨（peak 抬升）→回撤 2.9×ATR（不触发）→3.1×ATR（触发）→
  基础信号保持 1（**不得回补**）→基础信号 0→1（允许再入场）
- 暖机期不触发；截断重放无未来函数；overlay 关闭时 pipeline 结果 == 基础信号（恒等）
- 三入口一致性：同一 df 同一配置，三个入口算出的目标仓位逐位相同

## 3. M3：200 日趋势过滤 + 时序动量

### 3.1 趋势过滤（overlay，Faber 风格）

`gate = adj_close > MA(200)`，最终仓位 = base AND gate。语义是"价格在长期均线
下方时不持有多头"——既挡入场也强制出场（跌破 200 日线离场）。暖机期（前 200 根）
gate=False，策略自然不入场，如实不装。顺序：**先 gate 后 atr_stop**（先决定
"这个环境能不能持有"，再管"持有后何时认输"）。

### 3.2 时序动量（第三个策略，证据最强的入场信号）

```python
class TSMomentum(Strategy):
    name = "tsmom"          # label = "时序动量"
    def __init__(self, lookback: int = 250):   # ≈12 个月
    # 持有条件：adj_close / adj_close.shift(lookback) - 1 > 0
```

依据：Moskowitz/Ooi/Pedersen (2012)，跨 58 个品种、上百年数据成立的时序动量效应。
暖机期（前 lookback 根）为 0。参数校验同既有纪律。
注册进 REGISTRY + settings.yaml 种子 + guide 策略对比表（标注证据强度：
时序动量★★★ / 双均线与唐奇安=经典教学样品）。

### 3.3 测试

- gate 的手算与边界（恰在均线上/下、暖机期）
- tsmom 手算（含 lookback 边界、除权日连续性——用 adj_close 所以天然连续，测钉住）
- 三策略 × overlay 开/关的组合冒烟；无未来函数截断重放

## 4. M4：文档、指南与实测数字更新

- 「使用说明」页新增**「自定义策略」**一节：完整代码示例（含参数校验模板）+
  注册两步 + 三条硬规则（只用当日及以前数据 / 价格用 adj_close / "不含当日"窗口
  自己 shift(1)，并引用唐奇安死信号的教训）+ 提示"引擎的错位测试会抓未来函数"。
- 策略对比表扩成三列（含 tsmom），加"证据强度"行；overlay 单独一节讲清
  "止损不是策略，是覆盖层"。
- **重跑真实回测并更新事实数字**：overlays 默认开启会改变回测结果，README 与
  guide.FACTS 里的 116.1%/48.9% 是无叠加层的旧口径。处理：README 的实测表格
  改为**两套并列**（v0.1 基线·无叠加层 = 历史结论保留在案；v0.4 当前默认 =
  三策略 + 双叠加层的新实测），guide.FACTS 指向新表，测试框架
  （FACTS 必须逐字出现在 README）保证两边同步。**新数字必须来自真实重跑**，
  绝不许拍脑袋填。
- README 策略一节同步；`--strategy` 帮助文案含 tsmom。

## 5. 影响面备忘

- `journal/schema.SOURCES` 派生化后，既有 journal 测试若硬编码四元组需同步
- 扫描（只报 BUY）在趋势过滤开启后信号会变少（下行环境被 gate 掉）——这是特性，
  guide 里写明；`scan_meta` 不变
- 每日信号开始出现**止损触发的 SELL**——这正是给用户补的真缺口
- 回测耗时 ×1.5（三个策略），可接受

## 6. 不做

- MACD/KDJ/RSI 单独信号（证据不足，写进 guide 的"为什么没有"）
- 仓位管理/波动率目标（下一步的候选，本版不做）
- 指数级趋势过滤（用个股自身 200 日线，不引入指数对齐复杂度）
- 布林带/RSI(2) 反转（证据 ★★ 且以指数为主，观望）
