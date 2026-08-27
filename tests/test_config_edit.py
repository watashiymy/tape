"""config/settings.yaml 的外科式改写（v0.2.2 §3.2）。

这个文件驱动全部三个命令行脚本，写坏一次就全废，所以测试的重点不是
"新 universe 写进去了没"，而是**其余字节一个都没动**：注释（印花税分段依据、
各参数含义）丢失是静默灾难——脚本照样跑得通，人却再也不知道那些数字的由来。
"""
import os
import stat
from pathlib import Path

import pytest
import yaml

from quant.config_edit import replace_universe_block, write_universe

# 与 test_config.py 同样从 __file__ 推导：相对路径依赖 cwd。
REAL_CONFIG = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


def _outside_universe(text: str) -> tuple[str, str]:
    """把真实配置切成（universe 块之前, universe 块之后）两段。

    刻意不复用被测实现的定位逻辑：块前 = 第一次出现 'universe:' 之前的全部字节，
    块后 = 从下一个顶层键 '\\nbenchmark:' 起的全部字节。实现里定位错了，这里照样能发现。
    """
    return text[:text.index("universe:")], text[text.index("\nbenchmark:"):]


# ---------------------------------------------------------------- 真实配置 fixture

def test_real_config_only_universe_block_changes():
    """最关键的一条：拿真正被脚本加载的 config/settings.yaml 做 fixture，
    断言改写后非 universe 部分**逐字节**相同。yaml.safe_dump 那条路会重排键序、
    删光注释，且改完照样能 load——只有字节比对拦得住。"""
    original = REAL_CONFIG.read_text(encoding="utf-8")
    result = replace_universe_block(original, ["600519", "000333", "601318"])

    assert _outside_universe(result) == _outside_universe(original)
    assert result != original

    loaded = yaml.safe_load(result)
    assert loaded["universe"] == ["600519", "000333", "601318"]
    # 其余键的取值也必须与原文一致（字节相同已蕴含，这里是可读性更好的复述）
    assert loaded["benchmark"] == yaml.safe_load(original)["benchmark"]
    assert loaded["costs"] == yaml.safe_load(original)["costs"]
    assert loaded["scan"] == yaml.safe_load(original)["scan"]


def test_real_config_reloads_identically_except_universe(tmp_path):
    """脚本看到的不是字节而是 Settings：改写后除 universe 外每个字段都必须一模一样。
    字节比对管"人读到的"，这条管"三个脚本读到的"。"""
    import dataclasses

    from quant.config import load_settings
    original = REAL_CONFIG.read_text(encoding="utf-8")
    out = tmp_path / "settings.yaml"
    out.write_text(replace_universe_block(original, ["600519", "000333"]), encoding="utf-8")
    assert load_settings(out) == dataclasses.replace(
        load_settings(REAL_CONFIG), universe=("600519", "000333"))


def test_real_config_keeps_every_comment():
    """注释是这个文件里最容易被静默抹掉的东西，单独钉一条：
    原文里每一行带 # 的内容改写后仍原样存在。"""
    original = REAL_CONFIG.read_text(encoding="utf-8")
    result = replace_universe_block(original, ["600519"])
    comments = [ln for ln in original.splitlines() if "#" in ln]
    assert comments, "真实配置里应当有注释，否则这条测试形同虚设"
    for ln in comments:
        assert ln in result.splitlines(), f"注释行丢失: {ln!r}"


def test_real_config_roundtrip_is_idempotent():
    """改写产物再喂给自己必须稳定（多行 flow 是本函数自己的输出格式，
    它得能认得出自己写的东西）——否则面板每点一次按钮格式就漂移一次。"""
    original = REAL_CONFIG.read_text(encoding="utf-8")
    once = replace_universe_block(original, ["600519", "000333"])
    twice = replace_universe_block(once, ["600519", "000333"])
    assert twice == once


# ---------------------------------------------------------------- 各种既有写法

def test_single_line_flow():
    text = 'universe: ["600519", "000333"]\nbenchmark: "000300"\n'
    assert replace_universe_block(text, ["600519", "600036"]) == (
        'universe: [\n'
        '  "600519",\n'
        '  "600036"\n'
        ']\n'
        'benchmark: "000300"\n'
    )


def test_multi_line_flow_matching_committed_style():
    """仓库历史上的写法：跨行 flow，续行带缩进对齐。"""
    text = ('universe: ["600519", "600036", "601318", "600900", "000333",\n'
            '           "600030", "600276", "601088", "600887", "601899"]\n'
            'benchmark: "000300"\n')
    result = replace_universe_block(text, ["600519", "600036"])
    assert result == (
        'universe: [\n'
        '  "600519",\n'
        '  "600036"\n'
        ']\n'
        'benchmark: "000300"\n'
    )


def test_inline_comment_after_closing_bracket_is_kept():
    """`]` 之后的行内注释属于"其余字节"，必须原样保留。"""
    text = 'universe: ["600519"]   # 我的池子\nbenchmark: "000300"\n'
    result = replace_universe_block(text, ["600519", "000333"])
    assert result == (
        'universe: [\n'
        '  "600519",\n'
        '  "000333"\n'
        ']   # 我的池子\n'
        'benchmark: "000300"\n'
    )
    assert yaml.safe_load(result)["universe"] == ["600519", "000333"]


def test_universe_not_on_first_line():
    text = ('# 顶部说明\n'
            'benchmark: "000300"\n'
            'universe: ["600519"]\n'
            'backtest: {start: "2016-01-01", capital: 100}\n')
    result = replace_universe_block(text, ["000333"])
    assert result == ('# 顶部说明\n'
                      'benchmark: "000300"\n'
                      'universe: [\n'
                      '  "000333"\n'
                      ']\n'
                      'backtest: {start: "2016-01-01", capital: 100}\n')


def test_weird_whitespace_inside_value():
    text = ('universe:   [   "600519" ,\n'
            '\n'
            '        "000333"   ,   "600036"   ]   \n'
            'benchmark: "000300"\n')
    result = replace_universe_block(text, ["600519"])
    assert result == ('universe: [\n'
                      '  "600519"\n'
                      ']   \n'          # `]` 之后的尾随空白也是"其余字节"
                      'benchmark: "000300"\n')


def test_single_quoted_and_bare_values_are_accepted():
    """既有文件里可能是单引号甚至裸数字（裸写就是那个八进制坑），
    读的时候都得认，写出去统一成双引号。"""
    text = "universe: ['600519', 600036]\nbenchmark: \"000300\"\n"
    result = replace_universe_block(text, ["600519"])
    assert result == 'universe: [\n  "600519"\n]\nbenchmark: "000300"\n'


def test_universe_is_last_key_without_trailing_newline():
    text = 'benchmark: "000300"\nuniverse: ["600519"]'
    assert replace_universe_block(text, ["000333"]) == (
        'benchmark: "000300"\nuniverse: [\n  "000333"\n]')


def test_crlf_block_stays_crlf():
    """块内换行跟随原文，免得给文件掺进混合换行。"""
    text = 'universe: ["600519"]\r\nbenchmark: "000300"\r\n'
    result = replace_universe_block(text, ["600519", "000333"])
    assert result == ('universe: [\r\n'
                      '  "600519",\r\n'
                      '  "000333"\r\n'
                      ']\r\n'
                      'benchmark: "000300"\r\n')


def test_nested_key_named_universe_is_not_touched():
    """只认顶层 `universe:`。缩进下的同名键是别人的东西，不许动，
    也不许被算成"找到两个"。"""
    text = ('universe: ["600519"]\n'
            'scan:\n'
            '  universe: ["000333"]\n')
    result = replace_universe_block(text, ["600036"])
    assert result == ('universe: [\n  "600036"\n]\n'
                      'scan:\n'
                      '  universe: ["000333"]\n')


# ---------------------------------------------------------------- 拒绝而不是猜

def test_missing_universe_key_raises():
    with pytest.raises(ValueError, match="universe"):
        replace_universe_block('benchmark: "000300"\n', ["600519"])


def test_duplicate_universe_key_raises():
    """两个顶层 universe:（手改冲突/复制粘贴的常见后果）——改哪个都是猜。"""
    text = 'universe: ["600519"]\nbenchmark: "000300"\nuniverse: ["000333"]\n'
    with pytest.raises(ValueError, match="2 个|多个"):
        replace_universe_block(text, ["600036"])


def test_block_sequence_style_raises_instead_of_corrupting():
    """块序列写法本项目没用过，不去猜它的边界——猜错就是把 benchmark 一起吞掉。"""
    text = 'universe:\n  - "600519"\n  - "000333"\nbenchmark: "000300"\n'
    with pytest.raises(ValueError, match="flow"):
        replace_universe_block(text, ["600036"])


def test_unclosed_flow_raises():
    text = 'universe: ["600519",\n'
    with pytest.raises(ValueError, match="未闭合|闭合"):
        replace_universe_block(text, ["600036"])


def test_comment_inside_block_raises_rather_than_silently_dropping():
    """块内逐票注释没法跟着改写走。宁可报错让用户先挪走注释，
    也不能悄悄删掉——静默删注释正是本模块存在的理由。"""
    text = ('universe: ["600519",   # 茅台\n'
            '           "000333"]\n'
            'benchmark: "000300"\n')
    with pytest.raises(ValueError, match="注释"):
        replace_universe_block(text, ["600036"])


@pytest.mark.parametrize("bad", [[], ()])
def test_empty_symbols_raises(bad):
    """空池子会让两个入口报出误导性错误（v0.1.1 已修过一次），写之前就拦。"""
    with pytest.raises(ValueError, match="不能为空"):
        replace_universe_block('universe: ["600519"]\n', bad)


@pytest.mark.parametrize("bad", ["60051", "6005190", "60051a", "", '600519"', "600519\n"])
def test_bad_symbol_raises(bad):
    """非 6 位数字一律拒绝：既是业务规则，也是防 YAML 注入
    （带引号/换行的"代码"能把整个文件改成别的结构）。"""
    with pytest.raises(ValueError):
        replace_universe_block('universe: ["600519"]\n', [bad])


def test_symbols_are_quoted_so_leading_zeros_survive():
    """YAML 1.1 把裸写的 000333 当八进制读成 219，str() 之后是个静默错误的代码。
    产物必须带引号，且解析回来仍是字符串。"""
    result = replace_universe_block('universe: ["600519"]\n', ["000333", "000001"])
    assert '"000333"' in result
    loaded = yaml.safe_load(result)["universe"]
    assert loaded == ["000333", "000001"]
    assert all(isinstance(s, str) for s in loaded)


def test_duplicates_are_removed_keeping_first_position():
    result = replace_universe_block('universe: ["600519"]\n',
                                    ["000333", "600519", "000333"])
    assert yaml.safe_load(result)["universe"] == ["000333", "600519"]


# ---------------------------------------------------------------- 落盘：原子 + 复核 + 回滚

@pytest.fixture
def cfg(tmp_path) -> Path:
    """真实配置的副本——落盘测试也用真文件，别拿玩具 YAML 自欺欺人。"""
    p = tmp_path / "settings.yaml"
    p.write_text(REAL_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    return p


def test_write_universe_writes_and_keeps_everything_else(cfg):
    before = cfg.read_text(encoding="utf-8")
    write_universe(cfg, ["600519", "000333"])
    after = cfg.read_text(encoding="utf-8")
    assert _outside_universe(after) == _outside_universe(before)
    assert yaml.safe_load(after)["universe"] == ["600519", "000333"]
    assert list(cfg.parent.glob("*.tmp")) == []      # 原子写不留残渣


def test_write_universe_rolls_back_when_reload_raises(cfg, monkeypatch):
    """写后复核是最后一道闸。复核炸了就必须把原文件放回去——
    宁可这次操作失败，也不能留下一个坏配置（它驱动全部三个脚本）。"""
    before = cfg.read_bytes()

    def boom(_path):
        raise ValueError("模拟解析失败")

    monkeypatch.setattr("quant.config_edit.load_settings", boom)
    with pytest.raises(RuntimeError, match="回滚"):
        write_universe(cfg, ["600519", "000333"])
    assert cfg.read_bytes() == before
    assert list(cfg.parent.glob("*.tmp")) == []


def test_write_universe_rolls_back_when_reload_mismatches(cfg, monkeypatch):
    """解析成功但 universe 不是刚写的那份（例如文件里还有第二处生效的定义）——
    同样必须回滚：能 load 不等于写对了。"""
    import dataclasses

    import quant.config_edit as ce
    real = ce.load_settings
    before = cfg.read_bytes()

    def wrong(path):
        return dataclasses.replace(real(path), universe=("999999",))

    monkeypatch.setattr(ce, "load_settings", wrong)
    with pytest.raises(RuntimeError, match="回滚"):
        write_universe(cfg, ["600519", "000333"])
    assert cfg.read_bytes() == before


def test_write_universe_rejects_bad_input_before_touching_file(cfg):
    before = cfg.read_bytes()
    with pytest.raises(ValueError):
        write_universe(cfg, ["60051"])
    with pytest.raises(ValueError, match="不能为空"):
        write_universe(cfg, [])
    assert cfg.read_bytes() == before


def test_write_universe_keeps_file_mode(cfg):
    """mkstemp 建的临时文件是 0600，直接 os.replace 会把配置文件的权限一起换掉。"""
    os.chmod(cfg, 0o644)
    write_universe(cfg, ["600519"])
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o644


def test_write_universe_accepts_str_path(cfg):
    """面板与脚本传的都是字符串路径（'config/settings.yaml'）。"""
    write_universe(str(cfg), ["600519"])
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["universe"] == ["600519"]
