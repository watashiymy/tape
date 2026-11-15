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
