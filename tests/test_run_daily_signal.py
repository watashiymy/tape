# tests/test_run_daily_signal.py
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "run_daily_signal", Path(__file__).resolve().parent.parent / "scripts" / "run_daily_signal.py")
run_daily_signal = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(run_daily_signal)


def test_empty_strategy_config_aborts():
    """空策略表必须退出，不能空跑。

    "今日无新信号"是多数日子的正常结果，与"一个策略都没跑"输出**逐字相同**：
    同样打印无信号、同样写出只有表头的 CSV、同样退出码 0。
    config.py 的 `raw.get("strategies") or {}` 让 settings.yaml 里
    strategies 段缺失/为空/键名拼错时静默得到 {}，于是信号系统天天空跑且零告警。
    """
    with pytest.raises(SystemExit) as e:
        run_daily_signal.require_strategies({})
    assert "strategies" in str(e.value)


def test_valid_strategy_config_builds_strategies():
    """反向断言：正常配置必须照常构造出策略，守卫不能误伤。"""
    got = run_daily_signal.require_strategies(
        {"ma_cross": {"fast": 20, "slow": 60}})
    assert [s.name for s in got] == ["ma_cross"]


# ================================================================ 基准日：数据最新的那一天（v0.5.0）

import pandas as pd
from datetime import date

from tests.conftest import make_bars


def _bars(*days: str) -> pd.DataFrame:
    return make_bars([{"date": d, "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5,
                       "volume": 1000, "amount": 1e7} for d in days])


def test_latest_data_day_takes_the_max_so_one_suspended_stock_cannot_drag_everyone_back():
    """用最大值不用最小值：一只长期停牌的票会把最小值拖到几个月前，
    把所有人的信号都算成陈年旧账。最大值只会让停牌的那几只被判为落后并跳过。"""
    bars = {"A": _bars("2026-09-01", "2026-09-02", "2026-09-03"),
            "B": _bars("2026-09-01"),                          # 停牌
            "C": _bars("2026-09-01", "2026-09-02", "2026-09-03")}
    assert run_daily_signal.latest_data_day(bars) == date(2026, 9, 3)


def test_latest_data_day_refuses_an_empty_pool():
    with pytest.raises(ValueError):
        run_daily_signal.latest_data_day({})


def test_split_by_day_truncates_to_the_base_day():
    """**截断是硬要求**：--date 指历史某天时缓存里有那之后的 K 线，不截的话 scan()
    会拿最后一根（今天）算信号，产物文件名却写着历史那天——日期与内容对不上，
    且没有任何报错。"""
    bars = {"A": _bars("2026-09-01", "2026-09-02", "2026-09-03")}
    usable, stale = run_daily_signal.split_by_day(bars, date(2026, 9, 2))
    assert stale == {}
    assert usable["A"].index.max().date() == date(2026, 9, 2), "没截到基准日"
    assert len(usable["A"]) == 2


def test_split_by_day_marks_symbols_without_that_day_as_stale():
    """基准日那天没数据的（停牌、或数据还没到）算落后，连同它最后一根的日期一起返回，
    好让脚本打印「X: 最新 Y」。"""
    bars = {"A": _bars("2026-09-01", "2026-09-02"),
            "B": _bars("2026-09-01")}
    usable, stale = run_daily_signal.split_by_day(bars, date(2026, 9, 2))
    assert list(usable) == ["A"]
    assert stale == {"B": date(2026, 9, 1)}


def test_split_by_day_reports_everything_stale_when_nobody_has_the_requested_day():
    """--date 指了一个数据还没到的日子：全部落后，脚本据此非零退出，
    且报错前缀要与面板进度解析（progress._STALE）认的那句一致——由脚本源码钉住。"""
    bars = {"A": _bars("2026-09-01"), "B": _bars("2026-09-01")}
    usable, stale = run_daily_signal.split_by_day(bars, date(2026, 9, 4))
    assert usable == {} and set(stale) == {"A", "B"}
    src = Path(run_daily_signal.__file__).read_text(encoding="utf-8")
    assert "全部标的数据均未更新到 {base}，" in src, \
        "「数据未就绪」那句的前缀变了——runner/progress.py 的 _STALE 正则会认不出来"


def test_the_script_accepts_a_date_flag_and_does_not_hardcode_today():
    """与全市场扫描同一套用法：--date 指定基准日；留空 = 数据最新的那一天，
    不再要求"今天的数据到了才能跑"（那正是 17:30 前跑必失败的根因）。"""
    src = Path(run_daily_signal.__file__).read_text(encoding="utf-8")
    assert '"--date"' in src
    assert "latest_data_day(bars)" in src, "缺省基准日必须从数据里取，不是从日历里取"
    assert "baseday.require_trading_day" in src, "指定了 --date 就得先验它是交易日"
