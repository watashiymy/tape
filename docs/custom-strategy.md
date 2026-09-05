# 自定义策略：写一个接进来

> 这份说明原先是面板「使用说明」的第四页「自定义策略」，2026-09-05 起搬到这里：
> 它讲的是**怎么写代码**，读者是要改代码的人，不是用面板的人。面板上的文字只讲怎么用。

想把自己的想法接进来，一共**两步**——写一个类、注册一行。三个入口
（回测 / 信号跟踪 / 全市场扫描）、两个叠加层、面板的下拉框全都会自动认识它，
不用改任何别的地方。

## 第一步：写策略类

放在 `src/quant/strategy/` 下，比如 `my_break.py`：

```python
import pandas as pd

from quant.strategy.base import Strategy


class MyBreak(Strategy):
    name = "my_break"      # 内部键：会进产物文件名、CSV、交易日志，定了就永不改
    label = "我的突破"      # 显示名：面板与报告给人看的，随时可改

    def __init__(self, n: int = 30):
        # 构造期校验用 raise 而非 assert（python -O 会把 assert 剥掉）。
        # 不校验的代价全是静默的：参数写错往往不报错，而是产出一串"合法"的假仓位。
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ValueError("参数 n 必须是不小于 1 的整数，实际为 " + repr(n))
        self.n = n

    def generate_positions(self, df: pd.DataFrame) -> pd.Series:
        # df 已清洗对齐，含 open/high/low/close/volume/amount 与后复权 adj_* 列。
        # 返回与 df.index 对齐的 0/1 序列：1 = 目标持有，0 = 目标空仓。
        high_n = df["adj_close"].rolling(self.n).max().shift(1)  # 前 n 日最高，不含当日
        return (df["adj_close"] > high_n).astype(int)
```

## 第二步：注册

`src/quant/strategy/__init__.py` 顶部 import，`REGISTRY` 字典里加一行：

```python
    "my_break": MyBreak,
```

**让它真的跑得起来**（含 `--strategy` 单跑），还要往 `config/settings.yaml` 的 `strategies` 段加参数：

```yaml
strategies:
  my_break:
    n: 30
```

## 收尾：跑一次 pytest，补文案

从 `REGISTRY` 派生的那些地方立刻就生效了：面板控制台的策略下拉、「记账」页的来源筛选、
交易日志的来源合法值、报告里的显示名。但**不是所有东西都派生得出来**——加完请跑一次
`pytest`，会红几条，都是要你补文案的地方：

- `app/guide.py` 的**策略对比表**与策略下拉的 tooltip 要补上你这个策略
  （那张表是有观点的散文，派生不出来，也不该派生）；
- 改了 `config/settings.yaml` 就要同步 `tests/test_config.py` 里那份策略集合；
- 如果你的策略在测试夹具上也报信号，`tests/test_run_market_scan.py` 的两条
  行数断言要跟着调。

实测加第 4 个策略会红 5 条。如实写在这里，好过让三个月后的你先去翻扫描代码。

## 三条硬规则

违反了不会报错，只会给你一串"看起来能赚钱"的假回测：

1. **只用当日及以前的数据**。`shift(-1)`、全样本 min/max 归一化、`rolling` 完
   忘了错位，都是未来函数。引擎的错位测试（信号 T 收盘 → T+1 开盘成交）能抓住
   入口层的偷看，但抓不住策略内部的——写完先问自己一句：**这根 K 线收盘那一刻，
   这个值真的算得出来吗？**
2. **价格一律用 `adj_close`（后复权）**。raw close 在除权日有断崖：10 送 10 之后
   价格腰斩，你的"跌破均线"可能只是一次分红。成交量、成交额没有复权问题，照用。
3. **"前 N 日"窗口自己 `shift(1)`**。`rolling(n)` **含当日**：`收盘 > rolling(n).max()`
   永不成立（自己不会大于含自己的最大值），得到的是**全 0 的死信号**——回测照样
   跑完、零告警、零成交。唐奇安第一版就踩过这个坑，现在有回归测试钉着。

写完跑一次回测（缓存已热约 10 秒），到面板「个股K线」页把 ▲▼ 买卖点标注肉眼过一遍
——它比任何指标都先告诉你"信号跟想的不一样"。
