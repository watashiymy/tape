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
