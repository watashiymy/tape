# 打包元数据的诚实性（v0.5.0 设计 §2）。
#
# 这是全项目唯一一处「文档在撒谎且没有任何测试守着」的地方，代价全落在**别人**身上：
# 本机装着 openpyxl，所以 export.py 那句「已写进 pyproject.toml 的依赖」在这台机器上
# 永远看不出是假的；streamlit>=1.30 装得上、跑起来才崩。两者都属于典型的"我这儿能跑"。
#
# 版本号从此有单一事实来源：pyproject 与 README 首行由本文件对账，
# 散落三处各说各话的老毛病由测试钉住。
from __future__ import annotations

import re
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


@pytest.mark.parametrize("package", sorted(_requirements()))
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


def test_the_readme_never_hardcodes_a_test_count():
    """README 里**不许出现写死的测试条数**。

    这条最初写成"含 passed 的行必须也含 skipped"，结果放行了「1801 passed,
    1 skipped」——而同一批改动把总数推到了 1836，README 当场变成假数字。
    追着改数字是没有尽头的（下一批测试又会让它过期），所以直接禁掉这个形态：
    数字属于 pytest 的输出，不属于文档。

    这与 FACTS↔README 那套逐字对账并不矛盾：那些是**实测结论**（有唯一定义处 +
    有测试盯着），而测试条数每加一条用例就变，没有任何对账对象。
    """
    bad = [ln.strip() for ln in README.splitlines()
           if re.search(r"\d[\d,]*\s*(passed|failed)", ln)]
    assert not bad, (
        "README 写死了通过条数，下一批用例就会让它过期——改成不带数目的说法：\n"
        + "\n".join(bad))


def test_the_two_small_numbers_the_readme_does_quote_are_true():
    """README 保留了两个数：「1 skipped」与「3 deselected」。它们与总数不同——
    是**结构性**的（有几条条件跳过、有几项联网用例），所以不禁，改成验真。

    禁掉一个数最省事，但那样 README 就只能说"会有一些跳过"，读者反而判断不出
    自己看到的结果正不正常。既然能验，就验。
    """
    # **排除本文件**：它自己的源码里就含 "pytest.skip(" 与 "@pytest.mark.network"
    # 这两个字面量，不排就永远比真实数多一个（自指陷阱，与 git ls-files 查个人路径
    # 时同一个坑）。
    here = Path(__file__).resolve()
    sources = [q.read_text(encoding="utf-8") for q in (ROOT / "tests").glob("test_*.py")
               if q.resolve() != here]

    skips = sum(s.count("pytest.skip(") for s in sources)
    assert skips == 1, (
        f"条件跳过现在有 {skips} 条，而 README 写着「1 skipped」——"
        f"要么改 README，要么想清楚新增那条为什么要跳过")

    # 联网用例是**模块级** `pytestmark = pytest.mark.network` 打的标（不是逐个
    # 装饰器），所以按"带这个标的模块里有几个 test_ 函数"来数。
    import ast
    marked = 0
    for q in (ROOT / "tests").glob("test_*.py"):
        if q.resolve() == here:
            continue
        tree = ast.parse(q.read_text(encoding="utf-8"))
        module_marked = any(
            isinstance(n, ast.Assign)
            and any(getattr(tg, "id", "") == "pytestmark" for tg in n.targets)
            and "network" in ast.unparse(n.value)
            for n in tree.body)
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and n.name.startswith("test_"):
                decorated = any("network" in ast.unparse(d) for d in n.decorator_list)
                marked += bool(module_marked or decorated)
    assert marked == 3, (
        f"联网用例现在有 {marked} 项，而 README 写着「3 deselected」")


def test_no_upper_bound_excludes_the_version_actually_installed():
    """上界不许把**本机实测跑过的那一版**排除在外。

    这条是拿真事故换来的：v0.5.0 给依赖补上界时写了 `pyarrow>=15,<21`，
    而开发机上装的是 24.0.0——照 README 的安装命令跑一遍，pip 会把它**降级**到
    一个从没跑过测试的版本。"补上界防上游变动"本身是对的，但上界必须包含
    "我们真的跑过的那一版"，否则等于用一句没验证过的承诺换掉一句验证过的。
    """
    import importlib.metadata as md

    from packaging.requirements import Requirement
    from packaging.specifiers import SpecifierSet

    for spec in _requirements().values():
        req = Requirement(spec)
        try:
            installed = md.version(req.name)
        except md.PackageNotFoundError:
            continue                      # 没装就没有"实测过"这回事，跳过
        assert SpecifierSet(str(req.specifier)).contains(installed, prereleases=True), \
            (f"{req.name} 本机装的是 {installed}，却不满足 pyproject 的 {req.specifier}"
             f"——照 README 装一遍会把实测环境改掉")
