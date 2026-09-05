# 首份真实分钟行情来源

核查日期：2026-09-05。首选 **快期 / Shinny EDB 行情历史服务**，具体合约
`SHFE.rb2610`；不是主连、指数或拼接合约。

## 官方定义与使用边界

EDB 官方提供无需 token 的近一年分钟线下载，明确支持脚本、智能体、数据落盘和
初步回测。接口是 `GET https://edb.shinnytech.com/md/kline`，`period=60`；
查询字符串时间属于 `Asia/Shanghai`，返回的 `datetime_nano` 是 Unix 纳秒计的
**K 线起始时间**，`volume` 是该 K 线成交量，单位手。更早的分钟数据需要专业版权限，
不绕过限制。本项目只按需下载个人研究所需区间，不据此宣称拥有公开再分发权。
[官方接口、字段和权限说明](https://doc.shinnytech.com/edb/latest/md_server.html)

接口没有返回历史首次可得时间、发布延迟或修订轨迹，也没有承诺所下载历史值就是
当时首次发布值。应保留原始响应、请求参数、下载完成时间及 SHA-256。历史下载只能
标明为**事后取得的历史行情**：若研究假设每根 K 线结束即可观察到这些值，该假设必须
进入研究身份并展示给用户，不能把推定时刻写成来源已证明的历史 `available_at`。
取得时间也不能被当作历史发布时刻。这里的使用约束是本项目的建模决定。

## 首次下载实测

请求：`period=60`、`symbol=SHFE.rb2610`、
`start_time=2026-09-04 13:30:00`、`end_time=2026-09-04 15:00:00`。

- HTTP 200，响应类型 `text/csv; charset=utf-8`，5,380 字节。
- 本地下载完成核对时间 `2026-09-05T02:54:57Z`；响应 HTTP Date
  `2026-09-05T02:54:56Z` 是响应时间证据，不是逐条行情的历史发布时间。
- 原始响应 SHA-256：
  `7f969bd8e3db80de794edb016d867f81f811fed60b2283efab8cbaacc218bc44`。
- 共 90 根，起始时间为北京时间 13:30 至 14:59，每分钟恰好一条；无缺口、重复或
  零成交量记录，成交量合计 61,550 手。原始文件保留在本地验收材料，不提交到仓库。

以上是本次响应的检查结果，不是服务持续可用、数据绝对正确或研究已经验收的保证。

## 合约与模拟参数

上期所规定螺纹钢合约交易单位 10 吨/手，报价人民币元/吨，最小变动 1 元/吨；
下午交易时段为 13:30–15:00。当前闭环先使用这一个完整日盘子时段，未包含夜盘、
跨日结算或换月。[合约细则](https://www.shfe.com.cn/regulation/exchangerules/productrules/202512/t20251231_829962.html)、
[交易时间](https://www.shfe.cn/services/calenderandholidays/tradinghours/)

手续费、滑点、保证金和成交能力仍需分别建模，不能从 OHLCV 推出。尤其 RB2610
在 2026 年 9 月已进入交割前月，不能把最低保证金或更早公告费率当成当天实际参数；
使用的固定参数须明确标为模拟假设。[上期所风险参数调整公告](https://www.shfe.com.cn/publicnotice/notice/202605/t20260515_831713.html)

## 未采用的候选

曾小范围验证新浪 `getFewMinLine` 技术可达，但其字段文档未明确分钟开始/结束口径，
且官方用户协议限制未经许可的程序下载和源页面外展示。因此不将该候选作为本项目的
自动下载或行情再分发来源；AKShare 的代码许可不能代替上游数据授权。
[AKShare 上游实现](https://github.com/akfamily/akshare/blob/main/akshare/futures/futures_zh_sina.py)、
[新浪财经用户协议第 6、10 节](https://finance.sina.com.cn/roll/2021-05-12/doc-ikmxzfmm2033220.shtml)
