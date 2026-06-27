"""Streamlit dashboard for live monitoring and dataset exploration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import polars as pl
import streamlit as st
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from northstar_quant.config.settings import get_settings
from northstar_quant.config.trading_profile import (
    list_production_profiles,
    list_trading_profiles,
    load_trading_profile,
)
from northstar_quant.data.downloader import read_profile_manifest, validate_profile_data
from northstar_quant.data.overview import (
    build_data_overview_metrics,
    build_data_snapshot_table,
    build_normalized_price_frame,
    build_recent_candles,
    build_symbol_summary_table,
)
from northstar_quant.data.storage import load_profile_market_data
from northstar_quant.db.repositories import (
    aggregate_position_market_value,
    list_latest_positions,
    list_recent_fills,
    list_recent_orders,
)
from northstar_quant.db.session import SessionLocal


def render_dashboard() -> None:
    """Render the local monitoring dashboard."""

    settings = get_settings()
    st.set_page_config(page_title=f"{settings.app_name} Dashboard", layout="wide")
    st.title(f"{settings.app_name} \u5b9e\u76d8\u4e0e\u6570\u636e Dashboard")
    st.caption(
        "\u65e2\u53ef\u4ee5\u67e5\u770b\u5b9e\u76d8\u72b6\u6001\uff0c"
        "\u4e5f\u53ef\u4ee5\u76f4\u89c2\u67e5\u770b\u7b56\u7565\u6240\u4f7f\u7528\u7684\u884c\u60c5\u6570\u636e\u3002"
    )

    live_tab, data_tab = st.tabs(
        ["\u5b9e\u76d8\u603b\u89c8", "\u6570\u636e\u6982\u89c8"]
    )
    with live_tab:
        _render_live_overview(settings)
    with data_tab:
        _render_data_overview(settings)


def _render_live_overview(settings: Any) -> None:
    with SessionLocal() as session:
        positions = list_latest_positions(session)
        orders = []
        fills = []
        try:
            orders = list_recent_orders(session, limit=100)
        except OperationalError as exc:
            if not _render_legacy_schema_hint(exc):
                raise
        try:
            fills = list_recent_fills(session, limit=100)
        except OperationalError as exc:
            if not _render_legacy_schema_hint(exc):
                raise
        pos_value = aggregate_position_market_value(session)

        c1, c2, c3 = st.columns(3)
        c1.metric("\u6700\u8fd1\u6301\u4ed3\u6570", len(positions))
        c2.metric("\u6700\u8fd1\u8ba2\u5355\u6570", len(orders))
        c3.metric("\u6700\u8fd1\u6301\u4ed3\u603b\u5e02\u503c", f"{pos_value:,.2f}")

        st.subheader("\u6700\u8fd1\u6301\u4ed3")
        pos_df = pd.DataFrame(
            [
                {
                    "\u8d26\u6237": p.account,
                    "\u4ee3\u7801": p.symbol,
                    "\u6570\u91cf": p.qty,
                    "\u6210\u672c": p.avg_cost,
                    "\u5e02\u4ef7": p.market_price,
                    "\u5e02\u503c": p.market_value,
                    "\u65f6\u95f4": p.asof,
                }
                for p in positions
            ]
        )
        st.dataframe(pos_df, use_container_width=True)

        st.subheader("\u6700\u8fd1\u8ba2\u5355")
        orders_df = pd.DataFrame(
            [
                {
                    "\u753b\u50cf": o.profile_id,
                    "\u7b56\u7565": o.strategy_id,
                    "\u4ee3\u7801": o.symbol,
                    "\u65b9\u5411": o.side,
                    "\u6570\u91cf": o.qty,
                    "\u8ba2\u5355\u7c7b\u578b": o.order_type,
                    "\u9650\u4ef7": o.limit_price,
                    "\u8ba2\u5355\u8bed\u4e49": o.order_semantic,
                    "\u539f\u56e0": o.reason,
                    "\u8d26\u6237": o.account,
                    "\u76ee\u6807\u6743\u91cd": o.target_weight,
                    "\u53c2\u8003\u4ef7": o.reference_price,
                    "\u53c2\u8003\u4ef7\u6e90": o.reference_price_source,
                    "\u8ba1\u5212\u4ea4\u6613\u91d1\u989d": o.planned_trade_value,
                    "Planner": o.execution_planner_id,
                    "Run ID": o.run_id,
                    "Batch ID": o.batch_id,
                    "Plan ID": o.plan_id,
                    "\u5238\u5546\u8ba2\u5355\u53f7": o.broker_order_id,
                    "\u72b6\u6001": o.status,
                    "\u63d0\u4ea4\u65f6\u95f4": o.submitted_at,
                }
                for o in orders
            ]
        )
        st.dataframe(orders_df, use_container_width=True)

        st.subheader("\u6700\u8fd1\u6210\u4ea4")
        fills_df = pd.DataFrame(
            [
                {
                    "\u5238\u5546\u8ba2\u5355\u53f7": f.broker_order_id,
                    "\u4ee3\u7801": f.symbol,
                    "\u65b9\u5411": f.side,
                    "\u6570\u91cf": f.qty,
                    "\u4ef7\u683c": f.price,
                    "\u6210\u4ea4\u65f6\u95f4": f.filled_at,
                }
                for f in fills
            ]
        )
        st.dataframe(fills_df, use_container_width=True)

        st.subheader("\u6570\u636e\u5e93\u8fde\u901a\u6027")
        try:
            one = session.execute(text("select 1")).scalar()
            st.success(
                "\u6570\u636e\u5e93\u6b63\u5e38\uff0c\u63a2\u6d3b\u8fd4\u56de: "
                f"{one}"
            )
        except Exception as exc:  # pragma: no cover
            st.error(f"\u6570\u636e\u5e93\u5f02\u5e38: {exc}")

        report_dir = Path(settings.reports_dir)
        st.subheader("\u6700\u8fd1\u62a5\u544a\u6587\u4ef6")
        if report_dir.exists():
            files = sorted(
                report_dir.glob("*.md"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )[:20]
            file_df = pd.DataFrame(
                [
                    {
                        "\u6587\u4ef6\u540d": file.name,
                        "\u8def\u5f84": str(file),
                        "\u4fee\u6539\u65f6\u95f4": pd.to_datetime(file.stat().st_mtime, unit="s"),
                    }
                    for file in files
                ]
            )
            st.dataframe(file_df, use_container_width=True)
        else:
            st.info("\u62a5\u544a\u76ee\u5f55\u5c1a\u4e0d\u5b58\u5728\u3002")


def _render_legacy_schema_hint(exc: OperationalError) -> bool:
    message = str(exc)
    if "no such column" not in message:
        return False
    st.error(
        "\u5f53\u524d SQLite \u6570\u636e\u5e93\u7ed3\u6784\u6bd4\u4ee3\u7801\u65e7\uff0c"
        "\u8bf7\u5148\u8fd0\u884c `northstar init-db` \u8865\u9f50\u672c\u5730\u7f3a\u5931\u5b57\u6bb5\uff1b"
        "\u5982\u679c\u4f60\u662f\u6309 Alembic \u7ba1\u7406\u8fc1\u79fb\uff0c"
        "\u5219\u8fd0\u884c `alembic upgrade head`\u3002"
    )
    with st.expander("\u67e5\u770b\u6570\u636e\u5e93\u9519\u8bef\u8be6\u60c5", expanded=False):
        st.code(message)
    return True


def _render_data_overview(settings: Any) -> None:
    profiles = list_production_profiles() or list_trading_profiles()
    if not profiles:
        st.warning("\u5f53\u524d\u6ca1\u6709\u53ef\u7528\u7684\u4ea4\u6613\u753b\u50cf\u914d\u7f6e\u3002")
        return

    default_profile = settings.default_profile_id if settings.default_profile_id in profiles else profiles[0]
    selected_profile_id = st.selectbox(
        "\u9009\u62e9\u4ea4\u6613\u753b\u50cf",
        profiles,
        index=profiles.index(default_profile),
        help=(
            "\u5207\u6362\u4e0d\u540c\u753b\u50cf\u540e\uff0c"
            "\u4f1a\u52a0\u8f7d\u5bf9\u5e94\u7684\u6570\u636e\u96c6\u548c\u5143\u6570\u636e\u3002"
        ),
    )
    profile = load_trading_profile(selected_profile_id)

    st.caption(
        " | ".join(
            [
                profile.name,
                f"\u89d2\u8272: {profile.lifecycle.role}",
                f"\u4e3b\u7ebf: {profile.lifecycle.line_id}",
                f"\u7248\u672c: {profile.versions.profile}",
                profile.dimension_key,
                f"\u65f6\u533a: {profile.timezone}",
                f"\u65e5\u5386: {profile.calendar}",
                f"\u5e01\u79cd: {profile.currency}",
            ]
        )
    )

    try:
        manifest = read_profile_manifest(selected_profile_id)
        validation = validate_profile_data(selected_profile_id)
        market_df = load_profile_market_data(selected_profile_id)
    except FileNotFoundError:
        st.info(
            "\u5f53\u524d\u753b\u50cf\u8fd8\u6ca1\u6709\u843d\u76d8\u6570\u636e\u3002"
            "\u8bf7\u5148\u8fd0\u884c "
            f"`northstar data download --profile {selected_profile_id}`\u3002"
        )
        return
    except Exception as exc:  # pragma: no cover
        st.error(f"\u8bfb\u53d6\u6570\u636e\u6982\u89c8\u5931\u8d25: {exc}")
        return

    if market_df.is_empty():
        st.warning("\u5f53\u524d\u6570\u636e\u96c6\u4e3a\u7a7a\uff0c\u8fd8\u65e0\u6cd5\u7ed8\u56fe\u3002")
        return

    _render_data_metrics(manifest, validation)

    available_symbols = sorted(
        str(symbol) for symbol in market_df.get_column("symbol").unique().to_list()
    )
    if not available_symbols:
        st.warning("\u5f53\u524d\u6570\u636e\u96c6\u6ca1\u6709 symbol \u5185\u5bb9\u3002")
        return

    default_symbols = available_symbols[: min(6, len(available_symbols))]
    price_options = _build_price_options(market_df, manifest)

    control_left, control_right = st.columns([2, 1])
    selected_symbols = control_left.multiselect(
        "\u9009\u62e9\u6807\u7684",
        available_symbols,
        default=default_symbols,
        help=(
            "\u53ef\u591a\u9009\u67e5\u770b"
            "\u591a\u53ea\u6807\u7684\u7684\u5f52\u4e00\u5316\u8d70\u52bf\u3002"
        ),
    )
    price_label = control_right.radio("\u4ef7\u683c\u89c6\u89d2", list(price_options))
    price_column = price_options[price_label]

    chart_df = build_normalized_price_frame(
        market_df,
        price_column=price_column,
        symbols=selected_symbols,
    )
    st.subheader("\u5f52\u4e00\u5316\u4ef7\u683c\u8d70\u52bf")
    if chart_df.empty:
        st.info("\u5f53\u524d\u7b5b\u9009\u6761\u4ef6\u4e0b\u6ca1\u6709\u53ef\u7528\u4e8e\u7ed8\u56fe\u7684\u6570\u636e\u3002")
    else:
        chart_title = f"{price_label}\uff08\u8d77\u70b9\u5f52\u4e00\u5316\u4e3a 1.0\uff09"
        line_fig = px.line(
            chart_df,
            x="time",
            y="normalized_price",
            color="symbol",
            title=chart_title,
        )
        line_fig.update_layout(
            xaxis_title="\u65f6\u95f4",
            yaxis_title="\u5f52\u4e00\u5316\u4ef7\u683c",
            legend_title_text="\u6807\u7684",
            margin=dict(l=20, r=20, t=60, b=20),
        )
        st.plotly_chart(line_fig, use_container_width=True)

    st.subheader("\u5355\u6807\u7684\u6700\u8fd1 K \u7ebf")
    candle_left, candle_right = st.columns([3, 2])
    default_focus_symbol = selected_symbols[0] if selected_symbols else available_symbols[0]
    focus_symbol = candle_left.selectbox(
        "\u67e5\u770b\u5355\u53ea\u6807\u7684",
        available_symbols,
        index=available_symbols.index(default_focus_symbol),
    )
    candle_limit = candle_right.slider(
        "\u6700\u8fd1\u5c55\u793a\u6839\u6570",
        min_value=30,
        max_value=300,
        value=120,
        step=10,
    )
    candle_df = build_recent_candles(market_df, symbol=focus_symbol, limit=candle_limit)
    if candle_df.empty:
        st.info("\u5f53\u524d\u6807\u7684\u6ca1\u6709\u53ef\u663e\u793a\u7684 K \u7ebf\u6570\u636e\u3002")
    else:
        st.plotly_chart(_build_candlestick_figure(candle_df, focus_symbol), use_container_width=True)

    summary_col, snapshot_col = st.columns([3, 2])
    with summary_col:
        st.subheader("\u6807\u7684\u8986\u76d6\u6458\u8981")
        summary_df = build_symbol_summary_table(market_df)
        st.dataframe(summary_df, use_container_width=True)
    with snapshot_col:
        st.subheader("\u6700\u65b0\u539f\u59cb\u6570\u636e\u5feb\u7167")
        snapshot_source = market_df
        if selected_symbols:
            snapshot_source = market_df.filter(pl.col("symbol").is_in(selected_symbols))
        snapshot_df = build_data_snapshot_table(snapshot_source, limit=50)
        st.dataframe(snapshot_df, use_container_width=True)

    with st.expander("\u67e5\u770b manifest \u5143\u6570\u636e", expanded=False):
        st.json(manifest)
    with st.expander("\u67e5\u770b\u6821\u9a8c\u7ed3\u679c", expanded=False):
        st.json(validation)


def _render_data_metrics(manifest: dict[str, Any], validation: dict[str, Any]) -> None:
    metrics = build_data_overview_metrics(manifest)

    row1 = st.columns(4)
    row1[0].metric("\u6570\u636e\u6e90", str(metrics.get("data_source") or "-"))
    row1[1].metric("\u6570\u636e\u884c\u6570", _format_int(metrics.get("row_count")))
    row1[2].metric("\u6807\u7684\u6570\u91cf", _format_int(metrics.get("symbol_count")))
    row1[3].metric("\u8986\u76d6\u533a\u95f4", _format_range(metrics.get("start"), metrics.get("end")))

    row2 = st.columns(5)
    row2[0].metric("\u5e02\u573a", str(metrics.get("market") or "-"))
    row2[1].metric("\u8d44\u4ea7\u7c7b\u578b", str(metrics.get("asset_type") or "-"))
    row2[2].metric("\u6570\u636e\u9891\u7387", str(metrics.get("data_frequency") or "-"))
    row2[3].metric("\u518d\u5e73\u8861\u9891\u7387", str(metrics.get("rebalance_frequency") or "-"))
    row2[4].metric("\u5e01\u79cd", str(metrics.get("currency") or "-"))

    status = validation.get("status", "unknown")
    if status == "ok":
        st.success(
            "\u6570\u636e\u6821\u9a8c\u901a\u8fc7\uff1a"
            f"schema={validation.get('schema_version')}, "
            f"price_field={validation.get('configured_price_field')}"
        )
    else:  # pragma: no cover
        st.warning(f"\u6570\u636e\u6821\u9a8c\u72b6\u6001: {status}")


def _build_price_options(market_df: Any, manifest: dict[str, Any]) -> dict[str, str]:
    configured_price_field = str(manifest.get("price_field") or "close")
    options: dict[str, str] = {}

    labeled_columns = {
        "close": "\u539f\u59cb\u6536\u76d8\u4ef7\uff08close\uff09",
        "adjusted_close": "\u590d\u6743\u6536\u76d8\u4ef7\uff08adjusted_close\uff09",
    }
    ordered_columns: list[str] = []
    if configured_price_field in market_df.columns:
        ordered_columns.append(configured_price_field)
    for column in ("close", "adjusted_close"):
        if column in market_df.columns and column not in ordered_columns:
            ordered_columns.append(column)

    for column in ordered_columns:
        label = labeled_columns.get(column, f"\u4ef7\u683c\u5217\uff08{column}\uff09")
        options[label] = column

    if not options:
        options["close"] = "close"
    return options


def _build_candlestick_figure(candle_df: pd.DataFrame, symbol: str) -> go.Figure:
    figure = go.Figure()
    figure.add_trace(
        go.Candlestick(
            x=candle_df["time"],
            open=candle_df["open"],
            high=candle_df["high"],
            low=candle_df["low"],
            close=candle_df["close"],
            name=symbol,
        )
    )
    if "adjusted_close" in candle_df.columns:
        figure.add_trace(
            go.Scatter(
                x=candle_df["time"],
                y=candle_df["adjusted_close"],
                mode="lines",
                name="adjusted_close",
                line=dict(color="#ff7f0e", width=1.5),
            )
        )
    figure.update_layout(
        title=f"{symbol} \u6700\u8fd1\u884c\u60c5",
        xaxis_title="\u65f6\u95f4",
        yaxis_title="\u4ef7\u683c",
        xaxis_rangeslider_visible=False,
        margin=dict(l=20, r=20, t=60, b=20),
        legend_title_text="\u5e8f\u5217",
    )
    return figure


def _format_int(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _format_range(start: Any, end: Any) -> str:
    if start and end:
        return f"{start} -> {end}"
    return str(start or end or "-")


if __name__ == "__main__":  # pragma: no cover
    render_dashboard()
