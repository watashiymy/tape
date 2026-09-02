# 打包元数据的诚实性（v0.5.0 设计 §2）。
#
# 这是全项目唯一一处「文档在撒谎且没有任何测试守着」的地方，代价全落在**别人**身上：
# 本机装着 openpyxl，所以 export.py 那句「已写进 pyproject.toml 的依赖」在这台机器上
# 永远看不出是假的；streamlit>=1.30 装得上、跑起来才崩。两者都属于典型的"我这儿能跑"。
#
# 版本号从此有单一事实来源：pyproject 与 README 首行由本文件对账，
# 散落三处各说各话的老毛病由测试钉住。
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
README = (ROOT / "README.md").read_text(encoding="utf-8")


def _requirements() -> dict[str, str]:
    """{包名小写: 原始约束串}，主依赖与全部 extra 合在一起。"""
    proj = PYPROJECT["project"]
    specs = list(proj["dependencies"])
    for extra in proj.get("optional-dependencies", {}).values():
        specs.extend(extra)
    out = {}
    for spec in specs:
        name = spec.split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip()
        out[name.lower()] = spec
    return out


def test_the_version_matches_the_readme_title():
    """README 首行标题里的版本号就是 pyproject 的版本号。

    v0.4.0 之前 pyproject 一直停在 0.1.0（跨了 v0.2/v0.3/v0.4 三个版本没跟着涨）
    ——没人会主动去看那一行，所以只能靠测试提醒。
    """
    version = PYPROJECT["project"]["version"]
    first_line = README.splitlines()[0]
    assert f"v{version}" in first_line, \
        f"pyproject 写 {version}，而 README 首行是 {first_line!r}——两者必须一致"


@pytest.mark.parametrize("package", ["pandas", "streamlit", "baostock", "pyyaml",
                                     "plotly", "pyarrow", "openpyxl"])
def test_every_dependency_has_both_bounds(package):
    """下界 + 上界都要有。

    下界只承诺**实测跑过**的那一档：streamlit 曾写 >=1.30，而 st.navigation /
    st.Page / st.logo / st.iframe / ButtonColumn / width="stretch" 全都晚于它，
    照着装出来的环境必崩。上界是因为 baostock 0.9.3 的 get_data() 就被上游
    pandas 删掉 DataFrame.append 炸过一次（README §8 记着）。
    """
    spec = _requirements().get(package)
    assert spec, f"{package} 不在任何依赖列表里"
    assert ">=" in spec, f"{package} 没有下界：{spec}"
    assert "<" in spec, f"{package} 没有上界：{spec}"


def test_openpyxl_is_declared_because_the_export_path_needs_it():
    """Excel 导出的后端必须**被声明**——要么在依赖里，要么报错文案别说它在。

    历史问题：export.py 的报错写着「已写进 pyproject.toml 的依赖」，而它根本不在
    任何依赖列表里。本机恰好装着，所以这句假话在这台机器上永远暴露不了。
    """
    reqs = _requirements()
    assert "openpyxl" in reqs, "openpyxl 没被声明，而导出 Excel 必须有它"
    dev = [s.lower() for s in PYPROJECT["project"]["optional-dependencies"]["dev"]]
    assert any(s.startswith("openpyxl") for s in dev), \
        "dev 里必须含 openpyxl：少了它 tests/test_journal_export.py 等 8 项直接红"

    msg = (ROOT / "src" / "quant" / "journal" / "export.py").read_text(encoding="utf-8")
    assert "已写进 pyproject.toml 的依赖" not in msg, \
        "报错文案又在宣称它是主依赖了——它是可选依赖（extra: excel）"


def test_the_readme_does_not_promise_a_test_count_it_cannot_deliver():
    """README 不许出现「1782 passed」这种在新克隆上拿不到的数字。

    tests/test_journal_store.py 有全仓唯一一条条件跳过：本机没有
    journal/trades.csv（新克隆的正常状态）时 pytest.skip。所以陌生人跑出来的
    最好结果是「1781 passed, 1 skipped」，与 README 承诺的逐字不符。
    数字还会随每批新测试变旧，而它是 README 里唯一没进 FACTS↔README 对账机制的数字。
    """
    for line in README.splitlines():
        if "passed" in line and "deselected" in line:
            assert "skipped" in line, (
                f"README 这行承诺了一个新克隆拿不到的测试结果：{line.strip()!r}")
