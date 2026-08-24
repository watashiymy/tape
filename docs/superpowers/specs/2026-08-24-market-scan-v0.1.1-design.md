# 全市场每日信号扫描 v0.1.1 设计文档

- 日期：2026-08-24
- 状态：已获用户批准（数据方案与扫描范围经用户拍板）
- 前置：v0.1 已完工（126 项离线测试全绿）；本增量在审查修复批次之后实施

## 1. 目标与边界

**做什么**：收盘后一条命令，扫描全部**沪深主板非 ST** 股票（约 3200 只），输出当日新触发**买入**信号的清单，按当日成交额排序。

**定位闭环**：扫描是"雷达"，只报 BUY——全市场的 SELL 信号对未持仓者无意义；看中某只后把它加进 `universe` 固定池，此后的持有/卖出信号由现有 `run_daily_signal.py` 跟踪。

**不做**：回测不动（继续用固定池）；不含创业板/科创板/北交所/ST（用户拍板，与引擎 ±10% 假设一致，将来 top-K 回测可无缝衔接）；不做 akshare 快照加速（纯 baostock，用户拍板；接口留插槽，嫌慢再升级）；定时任务/推送仍属 v0.2。

## 2. 新增组件

```
src/quant/data/provider.py        # 接口增加 get_all_symbols()
src/quant/data/baostock_provider.py  # 实现之
src/quant/signal/market_scan.py   # 扫描纯逻辑（可离线单测）
scripts/run_market_scan.py        # 入口：编排 + 进度 + 汇总
config/settings.yaml              # 新增 scan: 段
app/dashboard.py                  # "今日信号"页加全市场扫描区块
output/scan/YYYY-MM-DD.csv        # 产物（gitignore 已覆盖 output/）
```

## 3. 接口与规则

### 3.1 get_all_symbols

```python
def get_all_symbols(self, as_of: date) -> pd.DataFrame:
    """全市场清单。列：symbol(6位str), name。
    过滤：仅主板（sh.60*/sz.00* 前缀）；名称含 'ST' 剔除；
    上市日期(ipoDate) 距 as_of 不足 400 自然日剔除（暖机不足的新股）。"""
```

数据源（探针已实测验证字段）：`query_all_stock(day)` 给出当日在市清单（code/tradeStatus/code_name），`query_stock_basic()` 给出 ipoDate/type/status。取二者交集：type=1（股票）、status=1（在市）。均用 `_fetch` 逐行迭代（禁 `get_data()`）。

### 3.2 config scan 段

```yaml
scan:
  history_days: 400        # 拉取历史窗口（自然日），约 270 根 K 线 > MA60 两倍
  min_avg_amount: 50000000 # 20 日均成交额门槛（元）：流动性差的票信号无意义且滑点假设失效
  top_n: 20                # 终端打印条数（CSV 存全量）
```

### 3.3 扫描逻辑（market_scan.py，纯函数，离线可测）

对每只标的的清洗后 bars（复用 DataService/prepare_bars），依次判定并**分类计数**：

| 判定 | 条件 | 归类 |
|---|---|---|
| 数据未更新 | `bars.index.max().date() < expected`（停牌/退市中） | stale |
| 暖机不足 | 行数 < 130（≈ MA60×2+10） | insufficient_history |
| 当前为 ST | 最新一根 `is_st == 1`（对名称过滤的兜底） | is_st |
| 流动性不足 | 20 日均成交额 < min_avg_amount | low_liquidity |
| 信号判定 | 各策略 `pos.iloc[-1]==1 且 pos.iloc[-2]==0` → 新 BUY | signal / no_signal |

信号记录字段：`date, symbol, name, strategy, close, pct_chg, amount, amount_ratio_20d`。
排序：当日成交额降序（简单、普适、流动性优先）。

### 3.4 入口脚本 run_market_scan.py

- 参数：`--config`（默认 config/settings.yaml）、`--limit N`（只扫前 N 只，试跑用）、`--date YYYY-MM-DD`（以指定交易日为基准扫描：将 bars 截断到该日并要求最新 bar 恰为该日；缺省用最近交易日——当日 17:30 前 baostock 未更新时会提示退出，与 run_daily_signal 行为一致）
- 主循环规模化设计（为 3200 只准备）：
  - **单票失败不中断**：取数异常时重试一次，仍失败则计数记录 symbol+原因，继续下一只，最后统一列出失败清单
  - **进度可见**：每 100 只打印一行进度（n/总数、累计信号数、耗时）
  - **中断可续**：依赖现有 parquet 缓存天然实现（重跑时已缓存的票秒过）
  - 空策略配置守卫（与 run_daily_signal 一致，联网前检查）
- 输出：终端 top_n 表 + 汇总统计（扫描 N、各类跳过计数、信号 Z、失败清单）；`output/scan/<expected>.csv` 全量
- 缓存共用 `data/cache/`（symbol 键控，schema 相同；scan 标的的 covered_start 从 today-400d 起，将来对其回测时头部回补机制自动补齐）

### 3.5 面板

"今日信号"页新增"全市场扫描"区块：读 `output/scan/` 最新 CSV，st.dataframe 展示 + 显示扫描日期；无文件时提示运行命令。只读，无按钮。

## 4. 测试策略

- **离线单测**（market_scan.py 纯函数）：主板前缀过滤、ST 名称过滤、上市天数过滤、五类判定的分类正确性与计数、排序、`--date` 截断语义。用 make_bars 构造。
- **network 集成测试**：get_all_symbols 返回 >1000 只、全部主板前缀、无 ST 名称。
- **验收**：`--limit 300` 真实跑通（约 5–8 分钟），报告各类计数与信号样例；全量首跑实测耗时写入 README（预估 30–60 分钟，之后每日增量 20–40 分钟）。

## 5. 已知取舍（如实记录）

- 逐票串行取数是 baostock 单连接的天花板，每日 20–40 分钟；接口分层已留好快路径插槽（如 akshare 全市场快照），需要时替换"当日 bar 来源"即可
- 排序用成交额而非策略强度分——简单可解释；策略特定的强度排序留给 top-K 组合（v0.2）
- 扫描池按"今天"的在市清单——对扫描无幸存者偏差问题（回测才有）
