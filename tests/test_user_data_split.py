# tests/test_user_data_split.py — 用户数据与项目配置的分界（v0.3.2 §2）
#
# 这个文件里全是 **git 层面**的断言，别的测试文件覆盖不到：一份代码可以完全正确，
# 而 .gitignore 少一行就把用户的真实交易记录推到了远端。两个方向都要钉：
#
#   1. 用户数据（交易日志、日志备份、本地信号池）**必须**被忽略、且**不被跟踪**；
#   2. 项目配置（config/settings.yaml）**必须继续**受版本控制——它是成本模型与
#      策略参数的出处，而且 tests/test_config_edit.py 与
#      tests/test_config.py::test_real_config_file 拿它当 fixture：
#      整份 gitignore 掉的话，那两处会在别人的新克隆上集体失败。
#
# `git check-ignore` 默认**也看索引**：一个已被跟踪的文件即使匹配 .gitignore 规则
# 也不算被忽略（要绕过索引得加 --no-index）。所以下面那条断言一举两得——
# 规则在、且文件真的没被跟踪。
import subprocess
from pathlib import Path

import pytest

from quant import config
from quant.journal import store

ROOT = Path(__file__).resolve().parent.parent

#: 属于用户的三份文件（相对仓库根）。与 .gitignore 里那三行一一对应。
USER_DATA = (
    "journal/trades.csv",           # 真实交易记录，不可再生，一推远端就全公开
    "journal/trades.csv.bak",       # 上一版备份（store.save_trades 落盘前自动另存）
    "config/universe.local.yaml",   # 本地信号池：暴露持仓与关注标的
)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


@pytest.mark.parametrize("rel", USER_DATA)
def test_every_user_data_path_is_ignored_and_untracked(rel):
    """防的是将来有人"顺手"删掉 .gitignore 里那几行，或者手工 `git add -f` 一次。
    两种都不会有任何报错——只会在某次 push 之后把私人数据公开出去。"""
    proc = _git("check-ignore", rel)
    assert proc.returncode == 0, (
        f"{rel} 没有被忽略（或它已被 git 跟踪）：这是用户数据，"
        f"不该进版本控制。git check-ignore 输出：{proc.stdout!r} {proc.stderr!r}")


@pytest.mark.parametrize("rel", USER_DATA)
def test_no_user_data_file_is_in_the_index(rel):
    """把上一条换个角度再钉一遍（check-ignore 的语义将来若变，这条仍然管用）。"""
    proc = _git("ls-files", "--error-unmatch", rel)
    assert proc.returncode != 0, f"{rel} 还在 git 索引里：先 git rm --cached 停止跟踪"


def test_the_journal_directory_itself_is_not_ignored():
    """忽略的是**那两个文件**，不是 `journal/` 整个目录（设计 §2.1 明写）。
    整目录忽略的话，将来往里放个 README 都得跟 .gitignore 打架。"""
    proc = _git("check-ignore", "journal/")
    assert proc.returncode != 0, "journal/ 整个目录被忽略了：只该忽略里面那两个文件"


def test_settings_yaml_is_still_tracked():
    """项目配置必须留在 git 里：成本模型与策略参数是**项目决策**，改动值得留痕；
    而且两个测试拿这个文件当 fixture（见本文件头部注释）。"""
    proc = _git("ls-files", "--error-unmatch", "config/settings.yaml")
    assert proc.returncode == 0, \
        "config/settings.yaml 不在版本控制里了：它是项目配置，也是两个测试的 fixture"

    proc = _git("check-ignore", "config/settings.yaml")
    assert proc.returncode != 0, "config/settings.yaml 被 .gitignore 掉了"


def test_the_ignore_rules_name_the_files_not_the_whole_config_dir():
    """`config/` 整目录被忽略会把 settings.yaml 一起带走（新克隆直接跑不起来）。"""
    proc = _git("check-ignore", "config/")
    assert proc.returncode != 0, "config/ 整个目录被忽略了"


def test_the_code_and_the_ignore_rules_talk_about_the_same_paths():
    """.gitignore 里那三行与代码里的默认路径必须是**同一批**文件。
    某天有人把默认落点改到 `data/journal.csv`，忽略规则还指着老地方——
    那时用户的交易记录会安静地进入下一次提交。"""
    assert str(store.TRADES_PATH) == "journal/trades.csv"
    assert str(store.backup_path(store.TRADES_PATH)) == "journal/trades.csv.bak"
    assert str(config.local_universe_path(Path("config/settings.yaml"))) \
        == "config/universe.local.yaml"

    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for rel in USER_DATA:
        assert rel in ignored, f".gitignore 里没有 {rel} 这一行（现在靠什么规则命中？）"
