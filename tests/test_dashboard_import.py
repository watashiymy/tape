import ast
from pathlib import Path


def test_dashboard_syntax_ok():
    """streamlit 脚本无法直接 import 测试（顶层执行 UI 代码），至少保证语法正确。"""
    src = Path("app/dashboard.py").read_text(encoding="utf-8")
    ast.parse(src)
