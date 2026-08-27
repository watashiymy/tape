"""config/settings.yaml 的外科式改写（v0.2.2 设计 §3.2）：只重写 universe 块。

为什么不用 `yaml.safe_dump` 回写整份文件：它会重排键序、**删光全部注释**——
而这个文件里的注释记着印花税分段的依据、各参数的含义与量纲。改完照样能 load，
脚本照样跑得通，只有人再也不知道那些数字的由来。也不引入 ruamel.yaml（为一个
小功能加一个新依赖不值当）。

所以这里做纯文本变换：行扫描定位 `universe:` 的 flow 列表，只替换那一段，
其余字节**逐字节原样保留**。定位不到、定位到多个、或写法超出已知两种
（单行 flow / 多行 flow）时一律抛错——猜错的代价是把整个配置文件毁掉。
"""
from __future__ import annotations

import os
import re
import stat
import tempfile
from collections.abc import Sequence
from pathlib import Path

from quant.config import load_settings
from quant.universe import normalize_universe

# 只认顶层键（行首、无缩进）。`scan:` 底下若有同名子键，那是别人的东西。
_KEY_RE = re.compile(r"\Auniverse[ \t]*:")

_INDENT = "  "          # 产物格式：每行一只，两空格缩进，便于 diff 与人工编辑


def _newline_of(line: str) -> str | None:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return None


def _find_flow_end(lines: list[str], li: int, ci: int) -> tuple[int, int]:
    """从 lines[li][ci] 处的 `[` 开始扫到与之配对的 `]`，返回它的 (行, 列)。

    要跨行，要认引号（引号内的 `]`/`#` 不算），要挡块内注释——注释跟着改写走不了，
    静默删掉正是本模块要防的那类灾难，所以宁可报错让用户先把注释挪走。
    """
    depth = 0
    quote: str | None = None
    while li < len(lines):
        line = lines[li]
        while ci < len(line):
            ch = line[ci]
            if quote is not None:
                if ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
            elif ch == "#":
                raise ValueError(
                    f"universe 块内第 {li + 1} 行有行内注释，改写会把它弄丢；"
                    "请先把注释挪到块外再操作")
            elif ch in "[{":
                depth += 1
            elif ch in "]}":
                depth -= 1
                if depth == 0:
                    return li, ci
            ci += 1
        if quote is not None:
            raise ValueError(f"universe 块第 {li + 1} 行的引号未闭合，无法安全改写")
        li, ci = li + 1, 0
    raise ValueError("universe 的 flow 列表未闭合（找不到配对的 ]），无法安全改写")


def replace_universe_block(text: str, symbols: Sequence[str]) -> str:
    """把 YAML 文本里的 universe 块换成新列表，其余部分逐字节不动。

    支持既有的两种写法：单行 flow（`universe: ["a", "b"]`）与多行 flow（跨行的
    `[...]`）。产物统一为每行一只的多行 flow，`]` 之后的行内注释原样保留。
    找不到 universe 键 / 找到多个 / 值不是 flow 列表 → 抛错，不猜。
    """
    wanted = normalize_universe(symbols)          # 空池子与坏代码在动文件之前就拦掉
    lines = text.splitlines(keepends=True)
    hits = [(i, m) for i, ln in enumerate(lines) if (m := _KEY_RE.match(ln))]
    if not hits:
        raise ValueError("配置文件里找不到顶层 universe: 键，拒绝猜测写入位置")
    if len(hits) > 1:
        raise ValueError(
            f"配置文件里有 {len(hits)} 个顶层 universe: 键（行 "
            f"{[i + 1 for i, _ in hits]}），改哪个都是猜，请先手工合并")

    i, m = hits[0]
    rest = lines[i][m.end():]
    lead = len(rest) - len(rest.lstrip(" \t"))
    if not rest[lead:].startswith("["):
        # 块序列写法（`- 600519` 逐行）本项目从没用过；猜它的边界一旦猜错，
        # 就会把后面的键一起吞掉。
        raise ValueError(
            "universe 的值不是 flow 列表（[...]），本工具只改写 flow 写法；"
            f"实际为 {lines[i].rstrip()!r}")

    end_line, end_col = _find_flow_end(lines, i, m.end() + lead)
    nl = _newline_of(lines[i]) or _newline_of(lines[end_line]) or "\n"
    suffix = lines[end_line][end_col + 1:]        # `]` 之后的一切（行内注释、换行）

    body = f",{nl}".join(f'{_INDENT}"{s}"' for s in wanted)
    block = f"{lines[i][:m.end()]} [{nl}{body}{nl}]{suffix}"
    return "".join(lines[:i]) + block + "".join(lines[end_line + 1:])


def _atomic_write(path: Path, data: bytes) -> None:
    """先写临时文件再 os.replace（与 data/cache.py 同一模式）：
    中途断电/Ctrl-C 也不会留下半截配置文件——那会让三个脚本全部启动失败。"""
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if mode is not None:
            os.chmod(tmp, mode)   # mkstemp 给的是 0600，不复位就把配置的权限一起换了
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_universe(path: str | Path, symbols: Sequence[str]) -> tuple[str, ...]:
    """把新的 universe 落盘到 path（原子写），返回规范化后的池子。

    写完**立即 load_settings 复核**：解析失败或 universe 不等于预期 → 回滚原文件
    并抛 RuntimeError。宁可这次操作失败，也不能留下一个坏配置——它驱动全部三个脚本。
    """
    path = Path(path)
    wanted = normalize_universe(symbols)
    original = path.read_bytes()
    new_text = replace_universe_block(original.decode("utf-8"), wanted)
    _atomic_write(path, new_text.encode("utf-8"))
    try:
        got = tuple(load_settings(path).universe)
    except Exception as e:
        _atomic_write(path, original)
        raise RuntimeError(f"写入后复核失败，已回滚 {path}：{type(e).__name__}: {e}") from e
    if got != wanted:
        _atomic_write(path, original)
        raise RuntimeError(f"写入后复核不符，已回滚 {path}：期望 {list(wanted)}，实际 {list(got)}")
    return wanted
