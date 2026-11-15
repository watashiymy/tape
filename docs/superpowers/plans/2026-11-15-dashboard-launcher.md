# 面板启动入口 `scripts/run_dashboard.py` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 加第四个入口脚本 `scripts/run_dashboard.py`：PyCharm 里点 ▶、或终端一句短命令就起面板，本机地址写死、工作目录钉到仓库根，不用再背那条带参数的 streamlit 命令。

**Architecture:** 脚本只做三件事：`os.chdir` 到仓库根（streamlit 按当前工作目录找 `.streamlit/config.toml`）、拼出
`streamlit run app/dashboard.py --server.address 127.0.0.1 [extra...]`、调 `streamlit.web.cli.main`（`.venv/bin/streamlit` 这个命令本身就是这一行）。
不碰 `src/`、不碰面板代码与面板文案；README 三处长命令换成新写法；一个测试文件钉住参数拼法、工作目录与「顶层不 import streamlit」。

**Tech Stack:** Python 3.14、Streamlit 1.61.1、pytest。

**Spec:** `docs/superpowers/specs/2026-11-15-dashboard-launcher-design.md`。

**分支：** `feat/dashboard-launcher`（已检出，别切）。提交身份 `git -c user.name=CH3NQ1 -c user.email=khalilstar@qq.com commit`，
提交说明末尾空一行加 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`。所有命令在仓库根目录跑，Python 一律 `.venv/bin/python`。
**不许** `git checkout --` / `git stash`；变异自查用 `cp` 备份、`mv` 还原。**不许 push**（推送公开仓前先问用户）。
本计划文件在开工前已单独提交（Task 3 Step 7 要求工作区干净、Task 5 Step 2 数提交时都算上它）。

---

### Task 1: 脚本与测试

**Files:** Create `scripts/run_dashboard.py`；Create `tests/test_run_dashboard.py`

- [ ] **Step 1: 写失败的测试**（新文件 `tests/test_run_dashboard.py`，整份如下）

```python
# tests/test_run_dashboard.py — 入口④ 面板的启动脚本（2026-11-15）：
# 参数拼法、工作目录钉到仓库根、模块顶层不碰 streamlit。
# 像 test_run_market_scan.py 一样按路径加载脚本；ROOT 从本文件算，**不**读脚本自己的 ROOT
# （否则「DASHBOARD 指错目录」只在错路径恰好不存在时才被 is_file() 抓到）。
import ast
import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "run_dashboard.py"

_SPEC = importlib.util.spec_from_file_location("run_dashboard", SCRIPT)
run_dashboard = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(run_dashboard)


def test_argv_is_the_streamlit_command_with_loopback_pinned():
    """脚本 = `streamlit run app/dashboard.py --server.address 127.0.0.1`；路径是绝对的，与启动目录无关。"""
    assert run_dashboard.build_argv() == [
        "streamlit", "run", str(ROOT / "app" / "dashboard.py"), "--server.address", "127.0.0.1"]


def test_extra_options_are_appended_after_the_address():
    """换端口这类 streamlit 自己的选项原样透传：追加在最后，前五项不动。"""
    argv = run_dashboard.build_argv(["--server.port", "8502"])
    assert argv[:5] == run_dashboard.build_argv()
    assert argv[5:] == ["--server.port", "8502"]


def test_the_dashboard_path_exists():
    """指错路径时 streamlit 只会在启动时报错；这里先红。"""
    assert run_dashboard.DASHBOARD == ROOT / "app" / "dashboard.py"
    assert run_dashboard.DASHBOARD.is_file()


def _capture_streamlit_main(monkeypatch) -> dict:
    """把 streamlit 的命令入口换成假函数，记下被调用时的工作目录与 sys.argv（脚本在 main() 里才 import 它，
    所以在这里 patch 模块属性就够）。"""
    import streamlit.web.cli as cli
    seen: dict = {}

    def fake_main():
        seen["cwd"] = Path(os.getcwd())
        seen["argv"] = list(sys.argv)
        return 0

    monkeypatch.setattr(cli, "main", fake_main)
    return seen


def test_main_pins_cwd_to_the_repo_root_then_hands_over_to_streamlit(tmp_path, monkeypatch):
    """从别的目录启动也要先切到仓库根——不然 .streamlit/config.toml（主题、viewer 模式、默认只绑本机）一样都读不到。"""
    seen = _capture_streamlit_main(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["run_dashboard.py"])
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as e:
        run_dashboard.main(["--server.port", "8502"])
    assert e.value.code == 0
    assert seen["cwd"] == ROOT
    assert seen["argv"] == run_dashboard.build_argv(["--server.port", "8502"])


def test_main_without_explicit_argv_forwards_the_command_line(monkeypatch):
    """PyCharm 点 ▶ / 终端直接跑走的是 argv=None 这条路：命令行上多给的选项要原样带过去。"""
    seen = _capture_streamlit_main(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["run_dashboard.py", "--server.headless", "true"])
    with pytest.raises(SystemExit):
        run_dashboard.main()
    assert seen["argv"] == run_dashboard.build_argv(["--server.headless", "true"])


def test_streamlit_is_not_imported_at_module_top_level():
    """延迟 import 是「测试能按路径离线加载本脚本」的前提；真会犯的是顶层 `from streamlit.web.cli import main`。"""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    offenders = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            offenders += [a.name for a in node.names if a.name.split(".")[0] == "streamlit"]
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "streamlit":
            offenders.append(node.module)
    assert not offenders, offenders
```

注意：这个文件里**不许出现** `pytest.skip(`——`tests/test_packaging.py` 数全部测试文件里这个字面量的个数、钉死为 1。

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest tests/test_run_dashboard.py -q`
Expected: 收集阶段就报错——`exec_module` 抛 `FileNotFoundError`（脚本还不存在；`spec_from_file_location` 对不存在的 .py 仍会返回 spec）。

- [ ] **Step 3: 写脚本**（新文件 `scripts/run_dashboard.py`，整份如下）

```python
"""面板 TAPE 的启动入口（入口④）：点一下就起，不用背那条带参数的 streamlit 命令。

    .venv/bin/python scripts/run_dashboard.py                        # 起面板，浏览器自动打开
    .venv/bin/python scripts/run_dashboard.py --server.port 8502     # streamlit 的选项原样透传（换端口等）

PyCharm 里：打开本文件，点 `if __name__ == "__main__":` 那行旁边的绿色 ▶（项目解释器已指向 .venv）。
跑过一次后它留在右上角的运行配置下拉里，之后 Ctrl+R 直接起；停止按钮停掉的就是 streamlit 本身。
新机器上第一次跑，streamlit 会在控制台问一次邮箱（~/.streamlit/credentials.toml 不存在时），直接回车跳过。

**它做的事**就是 `.venv/bin/streamlit` 那条命令加两个固定动作，没有第二套启动逻辑：
1. 工作目录切到仓库根。streamlit 按**当前工作目录**找 .streamlit/config.toml——主题、
   toolbarMode = "viewer"、gatherUsageStats = false、默认只绑本机，全在那份文件里；
   从别的目录起就一样都读不到、静默退回出厂默认（README §3）。
2. --server.address 127.0.0.1 写死。面板能在本机执行脚本，这个参数不能靠人记（README §3 的安全提示）；
   config.toml 已把它设成默认，这里再传一次是不把安全押在"工作目录一定对"上。
然后调 streamlit 命令本身的入口函数（.venv/bin/streamlit 这个文件里就是这一行）。

刻意不走的三条路：subprocess（PyCharm 的停止按钮要能直接停到 streamlit 本身，不留孤儿进程）、
streamlit.web.bootstrap.run（签名随版本变）、os.execv（Windows 上父进程立刻退出，PyCharm 会以为跑完了）。
streamlit 的 import 放在 main() 里：测试按路径加载本文件时不起 streamlit。
"""
from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "app" / "dashboard.py"
ADDRESS = "127.0.0.1"


def build_argv(extra: Sequence[str] = ()) -> list[str]:
    """`streamlit run app/dashboard.py --server.address 127.0.0.1 [extra...]` 的 argv。
    地址在前、extra 在后：换端口这类 streamlit 自己的选项直接透传。"""
    return ["streamlit", "run", str(DASHBOARD), "--server.address", ADDRESS, *extra]


def main(argv: Sequence[str] | None = None) -> None:
    os.chdir(ROOT)
    sys.argv = build_argv(sys.argv[1:] if argv is None else argv)
    from streamlit.web.cli import main as streamlit_main  # 延迟 import：见模块说明
    sys.exit(streamlit_main())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑，确认通过**

Run: `.venv/bin/python -m pytest tests/test_run_dashboard.py -q`
Expected: `6 passed`。

- [ ] **Step 5: 跑会扫全部 `scripts/*.py` 的两条既有约定与打包测试**

Run: `.venv/bin/python -m pytest tests/test_strategy_pipeline.py tests/test_packaging.py -q`
Expected: 全部 PASS（新脚本不调 `generate_positions(`、不碰策略参数；`pytest.skip(` 仍是 1 个）。

- [ ] **Step 6: 提交**

```bash
git add scripts/run_dashboard.py tests/test_run_dashboard.py
git -c user.name=CH3NQ1 -c user.email=khalilstar@qq.com commit -F - <<'MSG'
feat(launcher): 入口④ 面板的启动脚本 scripts/run_dashboard.py（PyCharm 点 ▶ 即可起面板）

chdir 到仓库根 + 本机地址写死 + 调 streamlit 命令本身的入口函数；streamlit 的选项原样透传。
测试钉住参数拼法、工作目录、argv=None 那条路与「顶层不 import streamlit」。

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
MSG
```

---

### Task 2: README、`app/dashboard.py` docstring、手册测试

**Files:** Modify `README.md`（L15、L167-173、L292-296、L507-512；行号都是改动前的，后面的步骤按原文找、别按行号）；Modify `app/dashboard.py:1,21-24`；Modify `tests/test_guide.py:504`

- [ ] **Step 1: 先把手册测试的守卫加上**（`tests/test_guide.py` 第 504 行，脚本名元组加 `"run_dashboard.py"`）

```python
    for script in ("run_market_scan.py", "run_daily_signal.py", "run_backtest.py", "run_dashboard.py"):
        assert script not in body, f"命令行一节不该再列脚本名 {script}"
```

Run: `.venv/bin/python -m pytest tests/test_guide.py -q -k cli_section`
Expected: PASS（手册的命令行一节本来就不列脚本名；这条从此钉住新脚本也不列）。

- [ ] **Step 2: README「5 分钟上手」**（第 15 行，整行替换）

旧：
```
.venv/bin/streamlit run app/dashboard.py --server.address 127.0.0.1
```
新：
```
.venv/bin/python scripts/run_dashboard.py                    # 起面板（PyCharm 里打开这个脚本点 ▶ 也行）
```

- [ ] **Step 3: README 入口④**（第 167–173 行：标题、命令块、括号注释，整段替换；第 175 行「面板自 v0.2.2 起叫 TAPE」那段不动）

旧：
````
### ④ 面板 TAPE：`streamlit run app/dashboard.py`

```bash
.venv/bin/streamlit run app/dashboard.py --server.address 127.0.0.1
```

（`--server.address 127.0.0.1` 不是可选项，原因见 §3 的安全提示。）
````
新：
````
### ④ 面板 TAPE：`scripts/run_dashboard.py`

```bash
.venv/bin/python scripts/run_dashboard.py                        # 起面板，浏览器自动打开
.venv/bin/python scripts/run_dashboard.py --server.port 8502     # streamlit 的选项原样透传（换端口等）
```

PyCharm 里不用敲命令：打开 `scripts/run_dashboard.py`，点 `if __name__ == "__main__":` 那行旁边的绿色 ▶（项目解释器已经指向 `.venv`）。跑过一次后它留在右上角的运行配置下拉里，之后 Ctrl+R 直接起；停止按钮停掉的就是 streamlit 本身。新机器上第一次跑，streamlit 会在控制台问一次邮箱，直接回车跳过。

（脚本把 `--server.address 127.0.0.1` 写死了、工作目录也钉到仓库根；手敲 `streamlit run` 时这个参数不是可选项，原因见 §3 的安全提示。）
````

- [ ] **Step 4: README §3 安全提示与命令块**（第 292–296 行，含命令块的收尾围栏）

旧：
````
> **安全提示**：面板会在**本机**执行脚本，等于一个本地命令执行入口。启动时**必须**加 `--server.address 127.0.0.1` 显式绑定回环地址，**切勿**用 `--server.address 0.0.0.0` 或任何内网地址。

```bash
.venv/bin/streamlit run app/dashboard.py --server.address 127.0.0.1
```
````
新：
````
> **安全提示**：面板会在**本机**执行脚本，等于一个本地命令执行入口。`scripts/run_dashboard.py` 已把 `--server.address 127.0.0.1` 写死、工作目录钉到仓库根；手敲 `streamlit run` 启动时**必须**自己加这个参数显式绑定回环地址，**切勿**用 `--server.address 0.0.0.0` 或任何内网地址。

```bash
.venv/bin/python scripts/run_dashboard.py
```
````
格式约束（`tests/test_dashboard_control_bar.py::test_readme_security_warning_is_a_paragraph_of_its_own` 钉着）：引用块仍是**一行**、前后空行、
400 字以内、字面上同时含 `--server.address` 与 `0.0.0.0`。紧随其后的出厂行为那段（`console` 围栏块及前后两段说明）**不动**。

- [ ] **Step 5: README §6 目录结构**（第 507–512 行）

旧：
```
├── scripts/
│   ├── run_backtest.py           # 入口①
│   ├── run_daily_signal.py       # 入口②
│   ├── run_market_scan.py        # 入口③ 全市场扫描（v0.1.1）
│   └── probe_baostock.py         # 一次性探针，验证 baostock 字段行为，不属于正式代码
├── app/                          # 入口④ Streamlit 面板 TAPE（v0.2.2 起按页拆分，见下）
```
新：
```
├── scripts/
│   ├── run_backtest.py           # 入口①
│   ├── run_daily_signal.py       # 入口②
│   ├── run_market_scan.py        # 入口③ 全市场扫描（v0.1.1）
│   ├── run_dashboard.py          # 入口④ 面板 TAPE 的启动脚本（PyCharm 点 ▶ 即可）
│   └── probe_baostock.py         # 一次性探针，验证 baostock 字段行为，不属于正式代码
├── app/                          # 入口④ 的面板代码（启动脚本见 scripts/run_dashboard.py；v0.2.2 起按页拆分，见下）
```

- [ ] **Step 6: `app/dashboard.py` 的 docstring**（第 1 行与第 21–24 行）

第 1 行旧：
```
"""Streamlit 本地面板 TAPE：streamlit run app/dashboard.py
```
新：
```
"""Streamlit 本地面板 TAPE。日常用 scripts/run_dashboard.py 起（PyCharm 点 ▶），等价于文末那条 streamlit run 命令。
```
第 21–24 行旧：
```
面板能执行本机命令，所以启动时**必须**显式绑回环地址：
    streamlit run app/dashboard.py --server.address 127.0.0.1
不加这个参数时 streamlit 监听 *:8501（所有网卡，v0.2.1 M3 实测），
切勿再用 --server.address 0.0.0.0 主动暴露到局域网。"""
```
新：
```
面板能执行本机命令，所以启动时**必须**显式绑回环地址：
    streamlit run app/dashboard.py --server.address 127.0.0.1
scripts/run_dashboard.py 把这个参数写死了、工作目录也钉到仓库根，日常用它起；手敲 streamlit run 时
参数不能省：不加时 streamlit 监听 *:8501（所有网卡，v0.2.1 M3 实测），
切勿再用 --server.address 0.0.0.0 主动暴露到局域网。"""
```

- [ ] **Step 7: 全量测试**

Run: `.venv/bin/python -m pytest -q`
Expected: 全部通过、1 skipped、6 deselected（README 的 §N 指针、安全提示段、`pytest.skip(` 计数、面板文案不含脚本名——都照常）。

- [ ] **Step 8: 确认老命令只剩该留的地方**

Run: `grep -rnE 'streamlit run app/dashboard\.py' README.md app/dashboard.py`
Expected: README 只剩 §3 出厂行为那个 ```console 块里的两行 `$ streamlit run ...`；`app/dashboard.py` 只剩 docstring 里那条参考命令（第 22 行）。
`.venv/bin/streamlit run` 这个写法在 README 里**一处都不剩**。

- [ ] **Step 9: 提交**

```bash
git add README.md app/dashboard.py tests/test_guide.py
git -c user.name=CH3NQ1 -c user.email=khalilstar@qq.com commit -F - <<'MSG'
docs(launcher): README 三处长命令换成 scripts/run_dashboard.py，补 PyCharm 用法；dashboard.py 说明同步；手册测试钉住新脚本名不进面板文案

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
MSG
```

---

### Task 3: 变异自查（每条至少一个测试红；改完**必须**还原）

**Files:** 临时改 `scripts/run_dashboard.py`；备份 `cp scripts/run_dashboard.py /tmp/run_dashboard.py.bak`，每条做完 `mv /tmp/run_dashboard.py.bak scripts/run_dashboard.py`（再 `cp` 一份备份做下一条）。

- [ ] **Step 1: 去掉 `os.chdir(ROOT)`**
  Run: `.venv/bin/python -m pytest tests/test_run_dashboard.py -q`
  Expected: `test_main_pins_cwd_to_the_repo_root_then_hands_over_to_streamlit` FAIL（cwd 是 tmp_path）。还原。
- [ ] **Step 2: `ADDRESS = "0.0.0.0"`**
  Expected: `test_argv_is_the_streamlit_command_with_loopback_pinned` FAIL。还原。
- [ ] **Step 3: `extra` 放到地址参数前面**（`["streamlit", "run", str(DASHBOARD), *extra, "--server.address", ADDRESS]`）
  Expected: `test_extra_options_are_appended_after_the_address` FAIL。还原。
- [ ] **Step 4: `DASHBOARD = ROOT / "dashboard.py"`**（指错目录）
  Expected: `test_the_dashboard_path_exists` 与 `test_argv_is_the_streamlit_command_with_loopback_pinned` FAIL。还原。
- [ ] **Step 5: 模块顶层加 `from streamlit.web.cli import main as streamlit_main`**（函数体内那行删掉）
  Expected: `test_streamlit_is_not_imported_at_module_top_level` FAIL。还原。
- [ ] **Step 6: `main()` 里改成 `sys.argv = build_argv(argv or sys.argv[1:])`**
  Expected: 两条 `main` 测试都还 PASS——这个变异**抓不到**（`argv=[]` 时才有区别，没人这么调），记一笔即可，不补测试。
- [ ] **Step 7: 还原后确认干净**
  Run: `git status --short && .venv/bin/python -m pytest tests/test_run_dashboard.py -q`
  Expected: 工作区无改动、`6 passed`。

---

### Task 4: 真机验收并把记录写进设计稿

**Files:** Modify `.claude/launch.json`（不进仓库）；Modify `docs/superpowers/specs/2026-11-15-dashboard-launcher-design.md`（加「## 6. 验收记录」）

- [ ] **Step 1: 给 Claude 的浏览器预览加一条「从别的目录起」的配置**（`.claude/launch.json` 的 `configurations` 数组末尾追加；绝对路径按本机）

```json
{
  "name": "tape-launcher",
  "runtimeExecutable": "sh",
  "runtimeArgs": [
    "-c",
    "cd /tmp && /Users/watashi/workspace/pycharm-project/quant_demo/.venv/bin/python /Users/watashi/workspace/pycharm-project/quant_demo/scripts/run_dashboard.py --server.port 8503 --server.headless true"
  ],
  "port": 8503
}
```
必须先 `cd /tmp`：从仓库根起的话 `config.toml` 本来就会被读到，证明不了「从别的目录也被读到」。

- [ ] **Step 2: 起服务，核启动日志**

用 `preview_start {name: "tape-launcher"}` 起；然后 `preview_logs` 看输出。
Expected: 恰有一行 `URL: http://127.0.0.1:8503`；**没有** `Local URL` / `Network URL` / `External URL`
（streamlit 1.61.1 的 `bootstrap._print_url`：地址被手动设定且非通配时只打一行 `URL:`，`Local URL` 那组是没钉地址才打的）。
人在终端验同一件事：`cd /tmp && <仓库>/.venv/bin/python <仓库>/scripts/run_dashboard.py --server.port 8503 --server.headless true`。

- [ ] **Step 3: 核监听地址**

Run: `lsof -nP -iTCP:8503 -sTCP:LISTEN`
Expected: 一行 `TCP 127.0.0.1:8503 (LISTEN)`；没有 `*:8503`。

- [ ] **Step 4: 核 config.toml 被读到**（这才是「工作目录钉到仓库根」的证据）

浏览器打开 `http://127.0.0.1:8503`，`read_page` / 截图：暗色主题（底色近黑、琥珀金主色）、侧栏品牌块 TAPE、
右上角**没有** Deploy 按钮（`client.toolbarMode = "viewer"` 生效）。
对照组（可选，证明 cwd 真的起作用）：`cd /tmp && <仓库>/.venv/bin/streamlit run <仓库>/app/dashboard.py --server.address 127.0.0.1 --server.port 8504 --server.headless true`
——同样绑本机，但没 chdir：右上角出现 Deploy 按钮、主题退回 streamlit 默认。核完立刻停掉 8504。

- [ ] **Step 5: 停掉服务**

`preview_stop`；再 `lsof -nP -iTCP:8503 -sTCP:LISTEN` 应为空。

- [ ] **Step 6: PyCharm 那一条留给用户**

打开 `scripts/run_dashboard.py` 点 ▶、浏览器自动打开面板、停止按钮能停——这一条我没法替用户点，验收记录里写明「待用户在 PyCharm 里确认」。

- [ ] **Step 7: 验收记录写进设计稿**（追加 `## 6. 验收记录（2026-11-15，真浏览器，Streamlit 1.61.1）`：三条证据各写实测值——那一行 URL、lsof 的那一行、
主题与 Deploy 按钮的观察；对照组若做了也记；PyCharm 一条标「待用户确认」；全量测试的通过数）

- [ ] **Step 8: 提交**

```bash
git add docs/superpowers/specs/2026-11-15-dashboard-launcher-design.md
git -c user.name=CH3NQ1 -c user.email=khalilstar@qq.com commit -F - <<'MSG'
docs(launcher): 启动脚本真机验收记录（从 /tmp 起：单行 URL、只听 127.0.0.1、config.toml 被读到）

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
MSG
```

---

### Task 5: 收尾

- [ ] **Step 1: 全量测试最后跑一遍**
  Run: `.venv/bin/python -m pytest -q`
  Expected: 全部通过、1 skipped、6 deselected。
- [ ] **Step 2: 看分支**
  Run: `git log --oneline main..HEAD`
  Expected: 设计稿、实施计划、脚本与测试、文档、验收记录——五到六个提交，没有别的。
- [ ] **Step 3: 合并与推送由用户定**：按 superpowers:finishing-a-development-branch 给选项（合回 main / 开 PR / 先放着）。**不 push。**
