# 用户数据与项目配置分离 v0.3.2 设计文档

- 日期：2026-08-29
- 状态：待实施（用户诉求：改 universe / 记一笔账，git 就提示有改动待提交）
- 前置：v0.3.1（1446 项离线测试全绿）

## 1. 问题

两处**用户数据**混在受版本控制的项目文件里，造成两个后果：

1. **日常摩擦**：改一次信号池、记一笔交易，`git status` 就脏一次，用户被反复提示提交与代码无关的东西；
2. **隐私风险**（更要紧，用户未提但真实存在）：`journal/trades.csv` 是完整的真实交易记录，
   `universe` 暴露持仓与关注标的。此仓库一旦推到任何远端，这些全部公开。

根因：`config/settings.yaml` 一个文件里混了两类东西——

| 类别 | 字段 | 该不该版本控制 |
|---|---|---|
| **项目配置**（决策，随代码演进） | benchmark / backtest / costs / strategies / scan | **该**，它们是项目决策，改动值得留痕、值得被测试钉住 |
| **用户状态**（个人，随交易变化） | universe | 不该 |

## 2. 方案

### 2.1 `journal/trades.csv` → gitignore（直接了当）

`load_trades` 文件不存在时已返回空表（见 store.py docstring），新克隆开箱即用，无需占位文件。

```
git rm --cached journal/trades.csv    # 停止跟踪，保留本地文件
```
`.gitignore` 增 `journal/trades.csv`（**不是** `journal/`——目录本身要留着，
将来若有 README 之类可放）。

### 2.2 `universe` → 本地覆盖文件

- `config/settings.yaml` 的 `universe` 保留，语义降级为**种子/默认值**（新克隆的起步池子），
  继续受版本控制，两个既有测试照旧可用。
- 新增 `config/universe.local.yaml`（**gitignore**）：存在时**覆盖**种子值。
  格式极简（只有一个列表），没有注释要保护：

```yaml
# 本地信号池（不受版本控制）。由面板「信号池」页写入，也可手改。
universe: ["002241", "002837"]
```

- `load_settings` 合并：本地文件存在则用它的 universe，否则用种子。
  **合并结果要能被调用方看出来源**（供面板提示"当前池子来自本地文件"）。
- `config_edit` 的写入目标改为本地文件。**顺带简化**：本地文件无注释可保，
  可整份重写，不必再走"外科式改写"那套（那套是为保住 settings.yaml 的中文注释而生的，
  对新文件是多余复杂度）。既有的 `replace_universe_block` 保留（settings.yaml 的种子仍可能被手改），
  但信号池页不再调它。

### 2.3 失败必须响亮

本地文件存在但损坏 / universe 为空 / 不是列表 → **抛错并指名文件路径**，
**不许静默回退到种子值**。静默回退 = 用户以为在跟踪 A 池子、系统实际跑 B 池子，
正是本项目一路在防的"不报错但结论错"。错误信息要给出可执行的出路
（"删除该文件即可回到 settings.yaml 的默认池子"）。

## 3. 替换掉被移除的安全网

gitignore `trades.csv` 等于拿掉了"git 历史 = 免费撤销"这层保护，而这份数据不可再生
（面板的 data_editor 支持批量编辑，一次误操作可能抹掉多行）。补一层轻量保护：

- `store.save_trades` 落盘前，把上一版另存为 `journal/trades.csv.bak`（单层，
  也 gitignore）。**先备份再写**，且备份走同样的原子替换。
- 面板「记账」页的历史表旁加一行小字，说明有备份文件及其路径。
- 不做多版本轮转（YAGNI）；导出 CSV/Excel 仍是用户自己的长期备份手段。

## 4. 影响面与测试

| 改动 | 测试影响 |
|---|---|
| `load_settings` 合并本地覆盖 | 新增：无本地文件用种子 / 有则覆盖 / 损坏抛错 / 空列表抛错 / 非列表抛错 |
| `config_edit` 写入目标改变 | 既有 `test_config_edit.py` 的主体仍测 `replace_universe_block`（保留）；新增本地文件的整份重写测试 |
| `store.save_trades` 加备份 | 新增：首次写无 .bak 不报错 / 二次写生成 .bak 且内容为上一版 / 备份失败不吞（要么成功要么响亮失败） |
| 面板信号池页 | AppTest：增删仍生效且写的是本地文件；settings.yaml 不被改动（**用 sha256 断言**） |
| `.gitignore` | 新增断言：`git check-ignore` 确认三个路径确实被忽略（防止将来有人误删规则） |

回归基线：1446 项离线 + 3 项联网。

## 5. 迁移（对当前用户）

用户现有的 7 只池子在 `settings.yaml` 的工作区改动里，需迁到本地文件且**不丢**：
实施时先读出当前值 → 写入 `config/universe.local.yaml` → 把 settings.yaml
恢复成 HEAD 版本（种子）。**做之前先备份用户当前文件**，做完逐值核对。

## 6. 不做

- 不用 `git update-index --skip-worktree`：它让"文件明明改了 git 却不报"，
  将来某次 pull 静默不生效时极难排查，对学习项目是陷阱而非便利。
- 不把整个 `config/settings.yaml` 移出版本控制（成本模型、策略参数是项目决策，
  且有两个测试钉住它）。
- 不做多版本备份轮转。
