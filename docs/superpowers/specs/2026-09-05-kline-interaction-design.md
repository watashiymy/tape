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

## 4. 第二轮：太细、太密（同日）

根源两个：① 日期轴给周末与节假日留空档，一周七格只画五根，每根被挤瘦约三成；
② 默认一次画全部——十年两千多根日线摊在一千像素上，每根不到一个像素。

- x 轴改**类目轴**（值 = 交易日 ISO 串），按交易日紧排，无空档；买卖点用同一套串落在同一根上。
  悬停标题直接显示日期串，`hoverformat` 不再需要。刻度 `nticks=8`。
- 新增「显示最近」：120 根 / 250 根 / 500 根 / 全部（`charts.KLINE_SPANS`），默认 120。
  按**根数**而不是时间跨度定范围——密度由根数决定：同样 120 根，日K是半年、周K两年多、年K是全部。
  切的是**数据**不是坐标范围：plotly 的 y 轴不会跟着 x 缩放重算，只设 x 范围会让最近半年挤在
  十年价格区间的一小段里。涨跌幅按全部 bar 算完再切（窗口首根不是 —）；买卖点先在全部 bar 上
  映射再只留窗口内的（直接在窗口上 searchsorted 会把窗口前的成交全堆到第一根）。
- K 线描边 2px → 1px：几百根挤在一起时描边比实体还宽，整根看着像一条线。
- `uirevision` 加入范围维度。

## 5. 第三轮：触控板双指缩放"画面抽动"（同日，macOS）

**根因（真机对照实验）**：同一张 120 根日K，各发 20 次带 ctrlKey 的 wheel 事件（macOS 触控板
双指缩放在浏览器里就是这种事件），记录每次之后横轴范围的起点：

| 宿主 | 横轴起点序列 |
|---|---|
| `st.plotly_chart` | 0.1 → 0.7 → … → 3.0 → **−0.5（回到初始）** → 0.1 → … → 3.0 → −0.5 → …（三轮锯齿） |
| 独立 plotly 页面 | 0.1 → 0.7 → … → 10.4（单调） |

Streamlit 的图表组件在 `onUpdate` 里 `setElementState(...); setFigure(n)`，而 react-plotly 在缩放
过程中的每个 `plotly_relayouting` 事件上都调用 `onUpdate`——于是每隔几次事件就有一次
`Plotly.react` 重建 `_fullLayout`，把 plotly 正在进行的缩放预览冲回初始范围。这是 Streamlit
组件的行为，图对象上没有任何开关能关掉它。

**修法**：K 线不再走 `st.plotly_chart`，改由项目自己的 iframe 组件承载（`app/kline_view.py` +
`app/kline_component/index.html`，只实现组件协议最小子集：componentReady / render /
setFrameHeight，不依赖 streamlit-component-lib）。plotly.min.js 通过
`declare_component(path=<已安装 plotly 包的 package_data 目录>)` 挂成静态路径，宿主页按
相对路径 `../<组件名>/plotly.min.js` 加载——不联网、不复制 4.8 MB 进仓库、版本与 Python 端一致。
`Plotly.react` + `uirevision` 让同一 iframe 跨重跑保留缩放位置。真机复测：单调推进，无回弹。

**代价**：K 线不再受 Streamlit 主题/选择事件管理（本项目本来就显式设色、不用选择）；
首次打开多加载一次 4.8 MB 的 plotly.js（本地，毫秒级）。
