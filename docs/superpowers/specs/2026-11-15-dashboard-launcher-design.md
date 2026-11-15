# 面板启动入口 `scripts/run_dashboard.py`（2026-11-15）

分支：`feat/dashboard-launcher`（从 main 拉出，main 已含趋势宽度正式图表 1527a2b）。做完合回 main；推送公开仓前先问用户。

## 0. 起因与用户决定

用户：「启动不太方便。之前一直都是通过一行命令启动的，这行命令很长，很难记住。有什么更优雅的方式？
或者告诉我在 PyCharm 里执行哪个方法能启动系统也行。」

查实（2026-11-15）：

- 那条命令是 `.venv/bin/streamlit run app/dashboard.py --server.address 127.0.0.1`。四个入口里 ①②③ 各是 `scripts/` 下
  一个脚本，PyCharm 里打开就能点 ▶；面板是唯一要靠背命令的入口。
- `.streamlit/config.toml` 自 v0.5.0 起已把 `server.address` 默认绑到 127.0.0.1，但 streamlit 按**当前工作目录**找它：
  从仓库根启动可以省掉地址参数，从别处启动就静默退回出厂默认（监听所有网卡）。README §3 为此要求那个参数「不是可选项」。
- `.venv/bin/streamlit` 这个命令本身只有三行：`from streamlit.web.cli import main; sys.exit(main())`。
- PyCharm 的项目解释器已指向 `.venv`（`.idea/misc.xml`），点 ▶ 用的就是正确环境。

给过三条路：A 加第四个入口脚本；B 在 pyproject `[project.scripts]` 登记 `tape` 命令；C 提交 PyCharm 共享运行配置。
**用户选 A**。B 的入口函数得进 `src/quant/`（业务包刻意不认识 `app/`，且只有可编辑安装才找得到仓库根），PyCharm 里也没有可点的东西；
C 的 XML 没法在 PyCharm 外验证，且 A 跑过一次后 PyCharm 自动把它留在运行配置下拉里，效果相同。

## 1. 脚本 `scripts/run_dashboard.py`

与 ①②③ 同一套路：模块 docstring 写用法，`if __name__ == "__main__": main()`。它不需要业务代码，**不**把 `src/` 加进 `sys.path`。

- `ROOT = Path(__file__).resolve().parent.parent`；`DASHBOARD = ROOT / "app" / "dashboard.py"`；`ADDRESS = "127.0.0.1"`。
- `build_argv(extra: Sequence[str] = ()) -> list[str]`：纯函数，返回
  `["streamlit", "run", str(DASHBOARD), "--server.address", ADDRESS, *extra]`。`extra` 原样追加在最后——换端口
  （`--server.port 8502`）、无头（`--server.headless true`）这些 streamlit 自己的选项直接透传，脚本不另起一套选项。
- `main(argv: Sequence[str] | None = None) -> None`：
  1. `os.chdir(ROOT)`：不管从哪里启动，`.streamlit/config.toml` 都被读到（主题、`toolbarMode = "viewer"`、
     `gatherUsageStats = false` 全在那份文件里，漏读等于主题与安全默认一起消失）。
  2. `sys.argv = build_argv(argv if argv is not None else sys.argv[1:])`。
  3. `from streamlit.web.cli import main as streamlit_main` → `sys.exit(streamlit_main())`。**import 放在函数体内**：
     模块顶层不碰 streamlit，测试才能离线按路径加载脚本（与 `app/guide.py` 不依赖 streamlit 是同一个理由）。

  这就是 `.venv/bin/streamlit` 那三行加固定参数，没有第二套启动逻辑。刻意不走的三条路：`subprocess`（PyCharm 的停止按钮
  要能直接停到 streamlit 本身，不留孤儿进程）、`streamlit.web.bootstrap.run`（签名随版本变，1.61 已是五个参数）、
  `os.execv`（Windows 上语义不同：父进程立刻退出，PyCharm 会以为跑完了）。
- docstring 写明两种用法：终端 `.venv/bin/python scripts/run_dashboard.py [streamlit 选项...]`；PyCharm 打开本文件、
  点 `if __name__ == "__main__":` 旁的 ▶，跑过一次后它留在右上角运行配置下拉里。再加一句：**新机器上第一次跑**，
  streamlit 会在控制台问一次邮箱（`~/.streamlit/credentials.toml` 不存在且非无头时），直接回车跳过；这台机器上那份文件已存在，
  不会问。少这句，日后会有一次「点了 ▶ 卡住不动」的误报。

## 2. 文档

- README 三处长命令（「5 分钟上手」第 4 行、入口④的命令块、§3 安全提示下的命令块）改为
  `.venv/bin/python scripts/run_dashboard.py`；入口④标题改为 `### ④ 面板 TAPE：\`scripts/run_dashboard.py\``，
  正文加一段 PyCharm 用法。入口④命令块正下方那句「（`--server.address 127.0.0.1` 不是可选项，原因见 §3 的安全提示。）」
  **同一次改掉**：换了命令之后它在断言一个看不见的参数——改成「脚本已把它写死；手敲 `streamlit run` 时它不是可选项，原因见 §3」。
- §3 安全提示那段改为：脚本已把本机地址写死、工作目录钉到仓库根；**手敲 `streamlit run` 时**仍必须加
  `--server.address 127.0.0.1`，切勿用 `--server.address 0.0.0.0` 或任何内网地址。**格式约束**：它仍是一个引用块、
  单独成段（前后空行）、400 字以内、至多 3 个换行，且字面上同时含 `--server.address` 与 `0.0.0.0`——
  `tests/test_dashboard_control_bar.py::test_readme_security_warning_is_a_paragraph_of_its_own` 钉着这几条。
  出厂行为那段 `console` 记录不动（它记的是 streamlit 的行为，不是本仓库的命令）。
- §6 目录结构里 `scripts/` 是逐个列脚本的（①②③ 各一行），加 `run_dashboard.py        # 入口④ 面板 TAPE 的启动脚本` 一行；
  `app/` 那行现在写着「入口④ Streamlit 面板 TAPE」，改成「入口④ 的面板代码（启动脚本见 scripts/run_dashboard.py）」，
  免得两行都叫「入口④」。
- `app/dashboard.py` docstring 第一行与「必须显式绑回环地址」那段各加一句：日常用 `scripts/run_dashboard.py` 起，
  它把这个参数写死了；老命令保留作参考。
- 面板内文案一字不动：手册的「命令行」一节只说「等价、去看 README」、不列脚本名，新脚本同样不列——
  `tests/test_guide.py::test_the_cli_section_points_at_the_readme_instead_of_listing_commands` 那个脚本名元组加上
  `"run_dashboard.py"`，让这条从「说法」变成「有测试红」。

## 3. 测试 `tests/test_run_dashboard.py`

像 `tests/test_run_market_scan.py` 一样用 `importlib` 按路径加载脚本。

- 测试里的 `ROOT` 从 `tests/__file__` 独立算出（同 `tests/test_run_market_scan.py`），**不**读脚本自己的 `ROOT`——
  否则「`DASHBOARD` 指错目录」只在错路径恰好不存在时才被 `is_file()` 抓到。
- `build_argv()`：首三项是 `["streamlit", "run", str(ROOT / "app" / "dashboard.py")]`（绝对路径）；紧接着
  `"--server.address", "127.0.0.1"`；`build_argv(["--server.port", "8502"])` 把这两项追加在**最后**、前五项不变。
- `DASHBOARD.is_file()`：脚本指错路径时 streamlit 只会在启动时报错，测试要先红。
- `main()`：monkeypatch `streamlit.web.cli.main` 为记录「调用时的 `os.getcwd()` 与 `sys.argv`」并返回 0 的假函数；
  `monkeypatch.chdir(tmp_path)` 起步，调 `main(["--server.port", "8502"])`；断言 `SystemExit` 的 code 是 0、
  记录到的 `Path(os.getcwd()) == ROOT`（ROOT 是 resolve 过的，macOS 上 getcwd 也返回解析后的路径；用 Path 比，免得 str/Path 不等）、
  `sys.argv == build_argv(["--server.port", "8502"])`。`sys.argv` 用 monkeypatch 还原。
- 用 `ast` 核脚本模块顶层没有 streamlit 的 import：遍历 `tree.body`，`ast.Import` 的 `names` 与 `ast.ImportFrom` 的 `module`
  都不许以 `streamlit` 开头——真会犯的那种是顶层 `from streamlit.web.cli import main`。延迟 import 是「测试能离线加载脚本」
  的前提；项目里 `tests/test_dashboard_import.py` 已有同类 AST 检查可仿。
- 新测试文件里**不许出现** `pytest.skip(`：`tests/test_packaging.py::test_the_two_small_numbers_the_readme_does_quote_are_true`
  数的是全部测试文件里这个字面量的个数、钉死为 1。「streamlit 装没装」也用不着 skip 守——它是硬依赖。
- 既有测试照常：`tests/test_packaging.py` 的 README 测试（「5 分钟上手」的 §N 指针、不写死测试数量）；
  `tests/test_dashboard_control_bar.py::test_readme_security_warning_is_a_paragraph_of_its_own`（见 §2 的格式约束）；
  `tests/test_strategy_pipeline.py` 会把 `scripts/*.py` 全部 glob 进去核两条约定，新脚本不调 `generate_positions(`、也不碰策略参数，自然通过。
- 变异自查：去掉 `os.chdir`、地址写成 `0.0.0.0`、把 `extra` 放到地址参数前面、`DASHBOARD` 指错目录、
  模块顶层 import streamlit——各至少一条测试红。

## 4. 验收（真机）

- 终端从**别的目录**（如 `/tmp`）跑 `<仓库>/.venv/bin/python <仓库>/scripts/run_dashboard.py --server.port 8503 --server.headless true`：
  启动日志只有一行 `URL: http://127.0.0.1:8503`，没有 `Local URL` / `Network URL` / `External URL`
  （streamlit 1.61.1 的 `bootstrap._print_url`：地址被手动设定——命令行或 config.toml——时只打一行 `URL:`；
  `Local URL:` 那一组恰恰是**没钉地址**时才打的，README §3 说的「两行 URL 不再打印」就是这个）；
  `lsof -nP -iTCP:8503 -sTCP:LISTEN` 显示 `127.0.0.1:8503`；页面是暗色主题、右上角没有 Deploy 按钮
  （三条合起来证明：地址写死生效、`config.toml` 从别的目录启动也被读到了）。
- PyCharm：打开脚本点 ▶，浏览器自动打开面板；停止按钮能停掉。这一条只能由用户在 PyCharm 里验收。
  我这边在 `.claude/launch.json`（不进仓库）加一条配置核对上一条，但它**必须先 `cd /tmp` 再起脚本**
  （`sh -c "cd /tmp && <绝对路径>/.venv/bin/python <绝对路径>/scripts/run_dashboard.py --server.port 8503 --server.headless true"`）：
  从仓库根起的话，`config.toml` 本来就会被读到，证明不了「从别的目录也被读到」；单行 `URL:` 与 `lsof` 两条只证明
  地址参数生效，与 cwd 无关，**暗色主题 + 没有 Deploy 按钮**才是 cwd 那条的证据。

## 5. 不做

- 不做 `tape` 命令（方案 B）、不提交 PyCharm 运行配置（方案 C）。
- 不动 `.streamlit/config.toml`、不改面板内任何文案。
- 不给脚本加自己的选项，不处理「端口已被占用」（streamlit 自己会报）。

## 6. 验收记录（2026-11-15，真浏览器，Streamlit 1.61.1）

实现提交 `eb79cc6`（脚本与 6 条测试）、`c72e6c9`（README / dashboard 说明 / 手册测试守卫）；全量 2320 通过、6 条联网测试不选
（这台机器上没有「1 skipped」：那条跳过只在从没记过一笔成交的新克隆上出现）。变异自查五条各红在点名的那条测试上；
第五条（顶层 import streamlit）只跑了 AST 那一条——顶层 import 会让测试里的假函数失效，两条 main 测试会真的把服务起起来；
第六条 `argv or sys.argv[1:]` 如预期抓不到。

两个实例都从 `/tmp` 起（`sh -c "cd /tmp && …"`，写在 `.claude/launch.json`，不进仓库）：

| | 新脚本 `run_dashboard.py --server.port 8503 --server.headless true` | 对照组 `streamlit run app/dashboard.py --server.address 127.0.0.1 --server.port 8504 --server.headless true`（不经脚本、没 chdir） |
|---|---|---|
| 启动日志 | 恰一行 `URL: http://127.0.0.1:8503`，无 `Local URL` / `Network URL` / `External URL` | 同样一行 `URL: http://127.0.0.1:8504`（地址是命令行给的，与 cwd 无关） |
| `lsof -nP -iTCP:<port> -sTCP:LISTEN` | `127.0.0.1:8503 (LISTEN)` | `127.0.0.1:8504 (LISTEN)` |
| 页面底色 `getComputedStyle(body)` | `rgb(18, 20, 26)` = config.toml 的 `#12141A` | `rgb(14, 17, 23)` = streamlit 出厂暗色 `#0E1117` |
| 右上角 Deploy 按钮 | **无**（`toolbarMode = "viewer"` 生效） | **有**（`stAppDeployButton` 存在，工具栏文字 "Deploy"） |

前两行两边一样：它们只证明地址参数生效。后两行两边不同：这才是「工作目录钉到仓库根、config.toml 从别的目录起也被读到」的证据。
两个实例打开的都是「市场概览」，标题 `TAPE · A股日线信号`；核完两个都已停掉。

**待用户在 PyCharm 里确认**：打开 `scripts/run_dashboard.py` 点 ▶，浏览器自动打开面板；停止按钮能停掉。
