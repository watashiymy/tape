# 面板任务控制台 v0.2.0 设计文档

- 日期：2026-08-25
- 状态：已获用户批准（控制范围经用户拍板：三个脚本全做）
- 前置：v0.1.1 已完工（226 项离线 + 3 项联网测试全绿）

## 1. 目标与边界

**目标**：把 Streamlit 面板从"纯只读"升级为**任务控制台**——三个脚本（全市场扫描 / 每日信号 / 回测）都能在页面上启动、停止、重跑，运行中有实时进度条与中间输出，完成后直接在页面呈现结果。

**背景**：v0.1 刻意把"面板触发能力"推迟（当时设计文档 §10 写明"v0.1 的 UI 为纯只读……触发能力留给 v0.2 评估"）。本次即该项评估的落地。

**不做**：不做任意命令执行（只暴露三个脚本的白名单参数）；不做多任务并行；不做远程访问/鉴权（仅 localhost）；不做定时调度（仍属后续）；命令行入口全部保留不变。

## 2. 核心难点与架构

Streamlit 每次交互重跑整个脚本，而扫描要跑 20–60 分钟，**绝不能阻塞主线程**。解法是"分离进程 + 状态落盘 + 轮询"：

```
按钮 → subprocess.Popen(argv列表, start_new_session=True, stdout/stderr→日志文件)
                            ↓
       状态写入 output/runs/<script>.json（run_id/pid/argv/log/started_at/status/exit_code）
                            ↓
       st.fragment(run_every="2s") 轮询：读状态 → 探活 → 读日志尾部 → 解析进度 → 渲染
```

`start_new_session=True`（独立会话/进程组）意味着关闭浏览器、甚至停掉 Streamlit 服务，运行中的任务都不会被杀；重开面板会自动认领仍在运行的任务并继续显示进度。与用户既有的 `nohup` 用法语义一致。

**分层**：进程管理与进度解析放 `src/quant/runner/`（纯逻辑、可离线单测），面板只做渲染。面板本身无法写单元测试，把逻辑挤出 UI 层是把缺陷挡在外面的关键手段。

## 3. 新增模块

```
src/quant/runner/__init__.py
src/quant/runner/process.py    # 进程生命周期
src/quant/runner/progress.py   # 日志 → 进度（纯函数）
src/quant/runner/jobs.py       # 三个任务的定义（argv 构造 + 参数校验 + 解析器绑定）
app/dashboard.py               # 新增"任务控制台"页 + 各页内嵌控制区块
output/runs/<script>.json      # 运行状态（gitignore 已覆盖 output/）
output/runs/logs/<run_id>.log  # 运行日志
```

### 3.1 process.py

```python
@dataclass(frozen=True)
class RunState:
    script: str; run_id: str; pid: int; argv: list[str]; log_path: str
    started_at: str; status: str          # running | success | failed | stopped
    exit_code: int | None; finished_at: str | None

def start(job_name, argv, runs_dir) -> RunState     # 启动并落盘状态
def read_state(job_name, runs_dir) -> RunState|None # 读状态，顺便做僵尸清理（见下）
def is_alive(pid) -> bool                           # os.kill(pid, 0) + PermissionError 视为存活
def stop(state) -> RunState                         # SIGTERM 进程组 → 10s → SIGKILL
def any_running(runs_dir) -> str | None             # 全局互斥检查，返回正在跑的任务名
```

**僵尸清理**：状态文件说 `running` 但 PID 已不存在 → 说明进程在写完状态前就退出了（被 kill -9、系统重启等）。此时从日志尾部推断结果：日志含"已保存:"视为 success，否则 failed，并落盘修正。**不做这一步的话，一次异常退出会让按钮永久禁用。**

**PID 复用防护**：仅靠 PID 探活在长时间跨度下有误判风险（PID 被系统复用给别的进程）。缓解：状态里存 `started_at`，探活时同时用 `psutil` 不可用则退化为"PID 存在 + 日志文件在最近 60 秒内有更新"的双条件判断。（psutil 不在依赖里，不新增依赖，用退化方案。）

### 3.2 progress.py（纯函数，本模块是单测重点）

```python
@dataclass(frozen=True)
class Progress:
    current: int | None; total: int | None      # 无进度信息时为 None（不确定态）
    elapsed_s: float | None; eta_s: float | None
    phase: str                                   # 人类可读的当前阶段
    extras: dict[str, str]                       # 如 {"信号": "15 条", "失败": "0 只"}

def parse_market_scan(log_text: str) -> Progress
def parse_daily_signal(log_text: str) -> Progress
def parse_backtest(log_text: str) -> Progress
def tail(log_text: str, n: int = 30) -> str
```

解析依据（对着脚本实际输出格式写，不臆测）：

| 任务 | 可解析的行 | 进度来源 |
|---|---|---|
| market_scan | `基准日 X，扫描池 N 只，策略: [...]` | total=N |
| | `[500/3010] 信号 15 条，失败 0 只，耗时 631s` | current/total/elapsed/extras；ETA = elapsed/current×(total−current) |
| | `已保存: output/scan/....csv` | phase=完成 |
| daily_signal | `[data] <代码>: ...` 计数 | current（total 取 universe 长度，从行数推断不可靠 → 不确定态 + 阶段文字） |
| | `===== X 信号 =====` / `已保存:` | phase |
| backtest | `[data] 600519: 2579 根K线 (...)` 计数 | current（total=universe 长度，由调用方传入） |
| | `===== ma_cross =====` | phase=回测中 |
| | `报告目录: output/...` | phase=完成 |

**ETA 只在有 current/total 时给出**，其余显示不确定态转圈 + 已用时长——宁可不显示，不显示假数字。

### 3.3 jobs.py

每个任务声明：显示名、脚本路径、参数 schema（类型/校验）、解析器、结果渲染方式。

**参数白名单与注入防护**（安全关键）：
- 绝不拼 shell 字符串；`subprocess.Popen` 用 **argv 列表**且 `shell=False`
- `--date`：必须匹配 `^\d{4}-\d{2}-\d{2}$` 且能被 `date.fromisoformat` 解析
- `--limit`：正整数，上限 10000
- `--strategy`：**只能从 `REGISTRY` 的键里选**（下拉，非自由输入）
- `--config`：不暴露给 UI（固定用默认配置），避免任意路径读取

## 4. 界面设计

### 4.1 新增"任务控制台"页

三张卡片（全市场扫描 / 每日信号 / 回测），每张包含：

- **状态徽标**：⚪ 空闲 / 🔵 运行中 / ✅ 成功 / ❌ 失败（退出码）/ ⏹ 已停止
- **参数控件**：扫描 `--limit`（number_input，留空=全量）+ `--date`（date_input，留空=最近交易日）；回测 策略下拉 + `--refresh` 勾选；每日信号 无参数
- **按钮**：`▶ 开始`（有任务运行时全局禁用，并提示"「扫描」正在运行"）、`⏹ 停止`（仅运行中可用）、`↻ 重跑`（沿用上次 argv）
- **进度**：有 current/total → `st.progress` + "1250/3010（41%），已用 12分38秒，预计剩余 ~19分钟"；否则 → 转圈 + 已用时长 + 阶段文字
- **实时输出**：日志尾部 30 行（`st.code`，等宽滚动）；`st.expander` 内放完整日志
- **完成后结果**：扫描 → 当次信号表格；回测 → 指标卡；每日信号 → 信号表格

刷新用 `@st.fragment(run_every="2s")` 包住卡片，仅局部刷新。**无任务运行时不设 run_every**，避免空转。

### 4.2 既有三页的内嵌控制区块

各页顶部加一个精简控制条（当前状态 + 开始/停止按钮），点击后跳转提示去控制台看详情。既有的只读展示逻辑完全不动。

### 4.3 侧边栏文案更新

原"本面板纯只读"改为说明可执行任务 + **安全提示**："本面板可在本机执行脚本，请勿通过 `--server.address 0.0.0.0` 暴露到局域网。"

## 5. 关键约束（违反会造成真实损害）

1. **全局互斥**：同时只允许一个任务运行。baostock 单会话，两个扫描并发会互踢下线且更慢。由 `any_running()` 在按钮层强制，不靠用户自觉。
2. **停止用 SIGTERM 而非 SIGKILL**：先向**进程组**发 SIGTERM，给脚本机会走完当前标的并让缓存原子落盘；10 秒未退再 SIGKILL。直接 -9 可能留下半截临时文件（虽然缓存层已做原子写，但仍应优先温和退出）。
3. **仅 localhost**：面板能执行本机命令，暴露到网络等同于远程命令执行漏洞。README 与侧边栏都要写明。
4. **日志文件不清理**：每次运行一个日志文件，保留历史便于事后排查；`output/` 已在 gitignore 内。

## 6. 测试策略

- **progress.py**：用**真实日志片段**（从 `/tmp/quant_full_scan.log` 与实际运行截取）做解析测试；边界：空日志、只有表头、中途截断的半行、异常 traceback、完成态。ETA 计算手算对照。
- **process.py**：用无害命令（`sleep 30`、`echo`、立即失败的 `python -c "import sys; sys.exit(3)"`）测 start/status/stop/exit_code 捕获/僵尸清理；SIGTERM 后进程确实消失（`is_alive` 转 False）。
- **jobs.py**：参数校验的正反用例（非法日期、负数 limit、未知策略名、试图注入 `; rm -rf` 的字符串必须被拒）。
- **面板**：`AppTest` 验证三张卡片渲染、无任务时的空态、有任务运行时开始按钮禁用。
- **真实验收**：面板启动 `--limit 30` 扫描 → 观察进度条推进与日志滚动 → 中途点停止 → 确认进程真的消失（`pgrep` 为空）、状态转为"已停止"、按钮恢复可用。

## 7. 里程碑

| 阶段 | 交付 | 验收 |
|---|---|---|
| M1 | `runner/{process,progress,jobs}.py` + 全套单测 | 纯逻辑测试全绿；用真实日志片段验证解析 |
| M2 | 控制台页面（三卡片 + 进度 + 日志 + 结果） | AppTest 通过；真实启动/停止扫描验收 |
| M3 | 既有三页内嵌控制条 + README + 侧边栏安全提示 | AppTest 全页通过；README 命令实跑 |

## 8. 已知取舍

- PID 探活在极端情况（PID 复用）可能误判，用"日志近期有更新"作为第二条件缓解；不引入 psutil 新依赖
- 不做任务队列：想连跑多个任务需手动等前一个结束，符合当前单人使用场景
- 进度 ETA 基于线性外推，冷/热缓存混合时会偏差（冷跑 3.2s/只、热跑 1.0s/只），仅作参考
