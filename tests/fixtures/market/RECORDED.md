# 首页数据源夹具（现场录制）

每个文件是数据源的**原始字节**，编码：腾讯 GBK、新浪 GB18030、东财 UTF-8。
`tests/test_market_sources.py` 对着它们写解析测试。按 spec §10 补录的盘前/竞价/收盘后
各时点文件名带 `_HHMM` 后缀。下面每行是一次录制。

- 2026-09-24 23:13:48 `tencent_quotes.bin` ← https://qt.gtimg.cn/q=sh000001,sz399001,sz399006,sh000688,sh000300,sh000852,bkqtRank_A_sh,bkqtRank_A_sz  HTTP 200, 3017 B
- 2026-09-24 23:13:50 `sina_quotes.bin` ← https://hq.sinajs.cn/list=sh000001,sz399001,sz399006,sh000688,sh000300,sh000852  HTTP 200, 1133 B
- 2026-09-24 23:13:51 `em_fenbu.bin` ← https://push2ex.eastmoney.com/getTopicZDFenBu?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt  HTTP 200, 316 B
- 2026-09-24 23:13:52 `tencent_minute_sh000001.bin` ← https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=sh000001  HTTP 200, 11819 B
- 2026-09-24 23:13:53 `tencent_minute_sz399001.bin` ← https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=sz399001  HTTP 200, 12081 B
- 2026-09-24 23:13:54 `tencent_minute_sz399006.bin` ← https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=sz399006  HTTP 200, 11571 B
- 2026-09-24 23:13:55 `tencent_minute_sh000688.bin` ← https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=sh000688  HTTP 200, 11093 B
- 2026-09-24 23:13:56 `tencent_minute_sh000300.bin` ← https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=sh000300  HTTP 200, 11703 B
- 2026-09-24 23:13:58 `tencent_minute_sh000852.bin` ← https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=sh000852  HTTP 200, 11714 B
- 2026-09-24 23:13:59 `tencent_kline_sh000001.bin` ← https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param=sh000001,day,,,5,qfq  HTTP 200, 2491 B
- 2026-09-24 23:14:00 `tencent_kline_sz399001.bin` ← https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param=sz399001,day,,,5,qfq  HTTP 200, 2529 B
- 2026-09-24 23:14:01 `em_ztpool.bin` ← https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=10000&sort=fbt:asc&date=20260924  HTTP 200, 13790 B
- 2026-09-24 23:14:02 `em_zbpool.bin` ← https://push2ex.eastmoney.com/getTopicZBPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=10000&sort=fbt:asc&date=20260924  HTTP 200, 2827 B

## 2026-09-27（周日，非交易日）整轮 `_sun1700`

实测结论：腾讯行情 [30] 停在 2026-09-24 16:14（不随请求时刻走）；新浪 [30]/[31] 停在 2026-09-24 15:44:00（上证）/15:00:03（深证）——非交易日新浪日期字段可靠，可当第二个交易日来源；分时 `data.date`=2026-09-24、`qt.market` 状态串解析为 close、242 点、最后一点 15:00；东财分布带 `qdate:20260924`，+11 档 52 = 涨停池 tc 52，−11 档 13；涨停池含北交所代码（920748，前缀 00/30/60/68/92）、无 ST；炸板池 10 只、前缀 00/30/60、无 ST；`newfqkline` sz399001 2026-09-22 成交额 1127554799000 元 = baostock sz.399106 的 1127554799048.82 元（万元取整）。

- 2026-09-27 16:40:35 `tencent_quotes_sun1700.bin` ← https://qt.gtimg.cn/q=sh000001,sz399001,sz399006,sh000688,sh000300,sh000852,bkqtRank_A_sh,bkqtRank_A_sz  HTTP 200, 3017 B
- 2026-09-27 16:40:37 `sina_quotes_sun1700.bin` ← https://hq.sinajs.cn/list=sh000001,sz399001,sz399006,sh000688,sh000300,sh000852  HTTP 200, 1133 B
- 2026-09-27 16:40:38 `em_fenbu_sun1700.bin` ← https://push2ex.eastmoney.com/getTopicZDFenBu?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt  HTTP 200, 316 B
- 2026-09-27 16:40:40 `tencent_minute_sh000001_sun1700.bin` ← https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=sh000001  HTTP 200, 11945 B
- 2026-09-27 16:40:41 `tencent_minute_sz399001_sun1700.bin` ← https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=sz399001  HTTP 200, 12207 B
- 2026-09-27 16:40:42 `tencent_minute_sz399006_sun1700.bin` ← https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=sz399006  HTTP 200, 11697 B
- 2026-09-27 16:40:43 `tencent_minute_sh000688_sun1700.bin` ← https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=sh000688  HTTP 200, 11219 B
- 2026-09-27 16:40:45 `tencent_minute_sh000300_sun1700.bin` ← https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=sh000300  HTTP 200, 11829 B
- 2026-09-27 16:40:46 `tencent_minute_sh000852_sun1700.bin` ← https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=sh000852  HTTP 200, 11840 B
- 2026-09-27 16:40:48 `tencent_kline_sh000001_sun1700.bin` ← https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param=sh000001,day,,,5,qfq  HTTP 200, 2617 B
- 2026-09-27 16:40:49 `tencent_kline_sz399001_sun1700.bin` ← https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param=sz399001,day,,,5,qfq  HTTP 200, 2655 B
- 2026-09-27 16:40:50 `em_ztpool_sun1700.bin` ← https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=10000&sort=fbt:asc&date=20260924  HTTP 200, 13790 B
- 2026-09-27 16:40:52 `em_zbpool_sun1700.bin` ← https://push2ex.eastmoney.com/getTopicZBPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=10000&sort=fbt:asc&date=20260924  HTTP 200, 2827 B
