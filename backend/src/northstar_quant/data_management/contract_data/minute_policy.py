"""Supplier aggregation evidence, separate from dated exchange session evidence."""

from typing import Any


def evidence() -> dict[str, Any]:
    """Return a fresh, serializable policy pin for each contract review.

    The user relayed Tushare customer support's reply on 2026-09-16. This is
    supplier evidence, not a published exchange rule or a dated session calendar.
    Never use the close grace periods as a historical availability timestamp.
    """
    return dict(
        rule="tushare-minute-support/1",
        source_kind="USER_RELAYED_SUPPLIER_SUPPORT",
        recorded_on="2026-09-16",
        source_label="Tushare 客服回复（用户转述，2026-09-16）",
        timestamp_convention="BAR_END",
        ordinary_interval="(start,end]",
        opening_interval="[start,end]",
        break_close_extension_seconds=300,
        final_close_extension_seconds=1800,
        historical_applicability_verified=False,
        available_at=None,
        confirmed=[
            "trade_time 为 K 线结束标签；普通区间左开右闭，开盘首根左闭右闭",
            "开盘首根可含集合竞价；无有效价格时供应商以昨收填充高开低收",
            "小节及夜盘结束向末根延长归集 5 分钟，最终收盘延长 30 分钟；不新增标签",
            "有夜盘时，日盘衔接示例首根为 09:01，覆盖 [09:00,09:01]；不强制每段都有开盘标签",
            "30/60 分钟按交易时长跨休市归集，不能按自然时钟直接切块",
        ],
        pending=[
            "各品种历史交易时段、集合竞价和夜盘启停的生效日期",
            "各周期首根、尾部不足周期及跨午休/夜盘的完整标签表",
            "非集合竞价零成交区间是否逐根填充，以及规则历史适用范围",
        ],
        research_constraint="结束标签不等于归集截止或首次可得时间；不据此提前向策略提供数据",
    )
