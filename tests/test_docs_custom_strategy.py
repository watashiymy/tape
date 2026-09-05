# tests/test_docs_custom_strategy.py — docs/custom-strategy.md（原面板「自定义策略」页）
#
# 2026-09-05 这份说明从面板搬到 docs/：它讲的是怎么写代码，读者是改代码的人。
# 搬家不许把纪律一起丢掉：模板代码仍要**真实导入并跑一段行情**（文档里的示例代码
# 没有测试就会腐烂成"照抄就报错"），三条硬规则仍要都在，README 要指得到它。
import importlib.util
import re
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "custom-strategy.md"
BODY = DOC.read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_the_doc_exists_and_the_readme_points_at_it():
    assert DOC.exists()
    assert "docs/custom-strategy.md" in README, "README 没指向自定义策略的文档"


def test_the_doc_has_the_two_steps_and_three_hard_rules():
    """两步注册 + 三条硬规则，缺一条都等于没讲——这三条恰好都是
    "违反了不报错、只给假回测"的坑，文档是唯一的防线。"""
    assert "REGISTRY" in BODY, "没讲注册那一步（REGISTRY 加一行）"
    assert "label" in BODY and "name" in BODY, "没讲内部键与显示名的分工"
    assert "adj_close" in BODY, "没讲「价格一律用后复权」这条硬规则"
    assert "shift(1)" in BODY, "没讲「前 N 日窗口自己 shift(1)」这条硬规则"
    assert "未来函数" in BODY, "没讲「只用当日及以前数据」这条硬规则"
    assert "死信号" in BODY, "没引用唐奇安全 0 死信号的教训"


def test_the_code_template_actually_works(tmp_path):
    """把文档里的 python 代码块存成临时模块**真实导入**，造一段行情验证它真能出仓位。"""
    m = re.search(r"```python\n(.*?)```", BODY, re.S)   # 第一个块就是完整模板（含 import）
    assert m, "文档里没有策略类的 python 代码块"
    path = tmp_path / "my_break_template.py"
    path.write_text(m.group(1), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("my_break_template", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)               # 模板类定义必须原样可执行
    strat = mod.MyBreak(n=3)
    close = [10.0, 11.0, 12.0, 11.0, 13.0, 12.0]
    df = pd.DataFrame({"adj_close": close},
                      index=pd.bdate_range("2026-01-05", periods=len(close)))
    pos = strat.generate_positions(df)
    # 手算：前 3 日最高（不含当日），第 5 根 13 > max(11,12,11)=12 → 1，其余 0
    assert list(pos) == [0, 0, 0, 0, 1, 0]
    with pytest.raises(ValueError):
        mod.MyBreak(n=0)                       # 校验模板必须真的在校验


def test_the_panel_no_longer_carries_the_custom_strategy_page():
    """搬家要搬干净：面板文案模块里不再有那一节、导航里不再有那一页。"""
    guide_src = (ROOT / "app" / "guide.py").read_text(encoding="utf-8")
    assert "guide_custom" not in guide_src
    assert "自定义策略" not in guide_src.split("GUIDE_PAGES", 1)[1].split(")\n\n", 1)[0]
