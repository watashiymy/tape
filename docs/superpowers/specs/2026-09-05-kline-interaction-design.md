# 「个股K线」交互重做（2026-09-05）

## 0. 起因（用户原话，四条）

1. 「买入」「卖出」信号把 K 线本身遮挡住了；
2. 鼠标悬停在 K 线上没有显示具体的价格信息（高、开、低、收、成交量、成交额）；
3. 缩放体验不好，只能通过右上角的工具栏缩放——能直接双指在图上缩放最好；
4. K 线应该支持日 K、周 K、年 K。

## 1. 做法

| 条 | 做法 | 落点 |
|---|---|---|
| 标注遮挡 | 买 ▲ 画在该根 **最低价下方**、卖 ▼ 画在 **最高价上方**（离影线 1.2% 价格），不再画在成交价上；真实成交价 / 股数 / 成交日进悬停 | `charts._marker_trace` |
| 悬停无信息 | 每根 bar 自带 hover 文本：开高低收、涨跌幅（对上一根收盘，首根 —）、成交量（万手/手）、成交额（亿/万/元）；`hovermode="x unified"`，买卖点与 bar 同框显示 | `charts._bar_hover_text`、`_cn_amount`、`_cn_volume` |
| 缩放 | `config={"scrollZoom": True, "doubleClick": "reset"}` 由 `st.plotly_chart(config=)` 传到前端：滚轮 / 触控板双指即缩放，拖动 = 平移（`dragmode="pan"`），双击复位；去掉底部缩略滑块 | `charts.KLINE_CONFIG`、`pages_backtest.page_kline` |
| 周期 | `resample_bars(df, freq)`：日线聚成周 / 月 / 年（开=首、高=max、低=min、收=末、量额求和），**bar 标在该周期内最后一个交易日**，没有交易日的周期不出空 bar；买卖点映射到所属那根 bar；页面加「周期」单选（日K/周K/月K/年K，内部键 D/W/M/Y） | `charts.resample_bars`、`KLINE_FREQS` |

另外：`uirevision` 随（标的, 周期）变——换图才重置视图，别的重跑保留用户缩放到的位置。图下一句话说清手势与 ▲▼ 的位置。

## 2. 不做
- 不隐藏周末/节假日的空档（plotly 的 rangebreaks 会拖慢渲染并干扰缩放；用户没提）。
- 不加成交量子图（用户要的是悬停看到量额，已满足）。
- 用户列了日/周/年，月 K 一并给了：同一段聚合代码、标准档位，少它反而奇怪。

## 3. 测试
- 重采样手算样例（两周 8 个交易日，第二周缺周五）、月/年、空周期、`D` 原样返回、未知周期响亮报错；
- 标注在影线之外（日 / 周两种周期）、映射到所属 bar、悬停 customdata 是真实成交；
- 悬停文本含开高低收量额与涨跌、单位换算；layout：pan / x unified / 无滑块；`KLINE_CONFIG` 真的经 `st.plotly_chart(config=)` 传到前端（AppTest 读 proto.config）；
- 页面：周期单选四档、切到周 K 出两根 bar（夹具是两周四天）。
- 真机（001337，周K）：181 根 bar、▲▼ 在影线外、dragmode=pan、hovermode=x unified、前端 `_context.scrollZoom=true`。
