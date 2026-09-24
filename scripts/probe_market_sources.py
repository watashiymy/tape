"""录制首页数据源的真实返回，当测试夹具（一次性探针，不是任务入口）。

    .venv/bin/python scripts/probe_market_sources.py tests/fixtures/market

每个来源一个文件，原样保存字节（腾讯 GBK、新浪 GB18030、东财 UTF-8 JSON），
并把录制时刻与 URL 追加到 RECORDED.md。解析测试对着这些字节写；
spec §10 要求的几个时点（8:50 / 9:16 / 9:26 / 9:31 / 9:45 / 15:00-15:40 每分钟）
用 --tag 区分（文件名带 tag）。
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

INDEXES = ("sh000001", "sz399001", "sz399006", "sh000688", "sh000300", "sh000852")
_Q = ",".join(INDEXES) + ",bkqtRank_A_sh,bkqtRank_A_sz"
UT = "7eea3edcaed734bea9cbfc24409ed989"
SOURCES = {
    "tencent_quotes": (f"https://qt.gtimg.cn/q={_Q}", {}),
    "sina_quotes": (f"https://hq.sinajs.cn/list={','.join(INDEXES)}",
                    {"Referer": "https://finance.sina.com.cn"}),
    "em_fenbu": (f"https://push2ex.eastmoney.com/getTopicZDFenBu?ut={UT}&dpt=wz.ztzt", {}),
}
for code in INDEXES:
    SOURCES[f"tencent_minute_{code}"] = (
        f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}", {})
for code in ("sh000001", "sz399001"):
    SOURCES[f"tencent_kline_{code}"] = (
        "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
        f"?param={code},day,,,5,qfq", {})


def pool_url(kind: str, day: str) -> str:
    return (f"https://push2ex.eastmoney.com/{kind}?ut={UT}&dpt=wz.ztzt&Pageindex=0"
            f"&pagesize=10000&sort=fbt:asc&date={day}")     # 与 sources.em_pool_url 同一串参数


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--tag", default="", help="文件名后缀，如 0916")
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"),
                    help="东财池子的 date 参数（交易日 YYYYMMDD）")
    args = ap.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sources = dict(SOURCES)
    sources["em_ztpool"] = (pool_url("getTopicZTPool", args.date), {})
    sources["em_zbpool"] = (pool_url("getTopicZBPool", args.date), {})
    log = out / "RECORDED.md"
    suffix = f"_{args.tag}" if args.tag else ""
    with log.open("a", encoding="utf-8") as f:
        for name, (url, headers) in sources.items():
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                r = requests.get(url, headers=headers, timeout=(2, 5))
                body, status = r.content, r.status_code
            except requests.RequestException as e:
                body, status = b"", f"ERR {e}"
            path = out / f"{name}{suffix}.bin"
            path.write_bytes(body)
            f.write(f"- {stamp} `{path.name}` ← {url}  HTTP {status}, {len(body)} B\n")
            print(f"{stamp} {name}: HTTP {status} {len(body)} B")
            time.sleep(1.0)           # 礼貌访问


if __name__ == "__main__":
    sys.exit(main())
