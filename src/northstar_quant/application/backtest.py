"""画像驱动离线回测的唯一编排入口。

本模块把已发布数据制品、策略管线、目标权重回测器和可审计运行清单固定为同一次
运行。它只读取已发布数据制品，绝不生成订单或调用券商。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import subprocess
from typing import Any

import polars as pl

from northstar_quant import __version__
from northstar_quant.research.validation.admission import evaluate_research_admission
from northstar_quant.research.backtest.metrics import periods_per_year_for_frequency
from northstar_quant.research.backtest.models import (
    BacktestAssumptions,
    BacktestCodeReference,
    BacktestContractError,
    BacktestDataReference,
    BacktestEngine,
    BacktestRequest,
    BacktestResult,
    RunManifest,
    TargetFrameReference,
)
from northstar_quant.research.backtest.registry import (
    resolve_target_backtester,
    run_target_backtest,
)
from northstar_quant.platform.common.enums import StrategyOutputType
from northstar_quant.platform.common.types import StrategyOutputBundle
from northstar_quant.platform.config.trading_profile import TradingProfile, load_trading_profile
from northstar_quant.data_platform.market.pit import (
    MarketDataPITSelector,
    MarketDataSnapshot,
)
from northstar_quant.data_platform.contracts.profile_governance import (
    validate_profile_data_governance,
)
from northstar_quant.data_platform.contracts.instrument_universes import (
    InstrumentUniverseMember,
    load_instrument_universe,
)
from northstar_quant.data_platform.sources.protocol import PublicationPurpose, PublicationScope
from northstar_quant.data_platform.quality.schema import (
    schema_version_for_profile,
    to_signal_market_data,
    validate_market_dataset,
)
from northstar_quant.data_platform.artifacts.storage import (
    dataset_manifest_path,
    load_json,
    load_profile_market_data,
    profile_config_sha256,
    profile_market_data_path,
)
from northstar_quant.portfolio_risk.portfolio.strategy_pipeline import (
    latest_pipeline_output,
    resolve_selected_profile_strategy_ids,
    run_profile_strategy_pipeline,
)


_EPSILON = 1e-12
_MIN_STATISTICAL_OBSERVATIONS = 20


@dataclass(frozen=True, slots=True)
class BacktestRun:
    """一次完整历史回测的冻结运行视图。

    ``manifest`` 仅包含可安全归档的配置、数据和代码指纹；不写入本地绝对路径、环境
    变量或下载器扩展选项，避免报告制品泄露运行环境信息。
    """

    profile: TradingProfile
    selected_strategy_ids: tuple[str, ...]
    backtester_id: str
    pipeline: StrategyOutputBundle
    latest_holdings: pl.DataFrame
    result: BacktestResult
    metrics: dict[str, object]
    analytics: dict[str, object]
    manifest: RunManifest

    @property
    def run_id(self) -> str:
        return self.manifest.run_id

    def assert_integrity(self) -> None:
        """在交给报告层前复验运行上下文没有偏离冻结清单。"""

        request = self.manifest.request
        if self.profile.profile_id != request.profile_id:
            raise BacktestContractError("回测画像 ID 与运行清单 request 不一致")
        if profile_config_sha256(self.profile) != request.profile_config_sha256:
            raise BacktestContractError("回测画像配置已偏离运行清单 request")
        if self.profile.dimension_key != request.profile_dimension_key:
            raise BacktestContractError("回测画像维度与运行清单 request 不一致")
        if self.selected_strategy_ids != request.selected_strategy_ids:
            raise BacktestContractError("已选策略与运行清单 request 不一致")
        if self.pipeline.output_type is not StrategyOutputType.TARGET_WEIGHT:
            raise BacktestContractError("运行清单只允许 target_weight 策略输出")
        current_target = TargetFrameReference.from_frame(
            self.pipeline.frame,
            time_column=self.pipeline.time_column,
        )
        if current_target != request.target:
            raise BacktestContractError("策略目标已偏离运行清单 request，已拒绝归档")

        self.manifest.verify_outputs(
            result=self.result,
            analytics=self.analytics,
            metrics=self.metrics,
        )

    def verified_latest_holdings(self) -> pl.DataFrame:
        """从已验证的目标权重重建报告持仓，忽略可变缓存字段。"""

        self.assert_integrity()
        return latest_pipeline_output(self.pipeline)

    def manifest_mapping(self) -> dict[str, object]:
        """返回已复验的清单投影；报告/CLI 不得自行拼装 dict。"""

        self.assert_integrity()
        return self.manifest.as_mapping()

    @property
    def artifact_period(self) -> str:
        self.assert_integrity()
        curve = _report_equity_curve(self.analytics)
        if not curve:
            raise ValueError("回测结果缺少净值曲线，无法确定报告周期")
        start = str(curve[0]["date"])[:10].replace("-", "")
        end = str(curve[-1]["date"])[:10].replace("-", "")
        return f"{start}-{end}"

    @property
    def period_label(self) -> str:
        self.assert_integrity()
        curve = _report_equity_curve(self.analytics)
        if not curve:
            raise ValueError("回测结果缺少净值曲线，无法确定报告周期")
        start = str(curve[0]["date"])[:10]
        end = str(curve[-1]["date"])[:10]
        return f"{start} 至 {end}"


def run_profile_backtest_run(
    profile_id: str | None = None,
    *,
    strategy_ids: Sequence[str] | None = None,
) -> BacktestRun:
    """运行 legacy 市场投影上的一次完整、可归档的历史回测。

    数据必须先通过 ``load_profile_market_data`` 的内容哈希和 schema 校验。策略使用完整
    历史生成信号，再交由画像唯一匹配的目标权重回测器执行；因此研究、CLI 回测和
    周期报告可以引用同一运行语义，而不会各自拼装流程。

    这个入口保留既有 ``storage/market`` / ``data_manifest_v3`` 行为，不能被标记为行级
    revision PIT 回放。需要冻结不可变 ``DatasetVersion`` 和明确 ``as_of`` 的研究输入时，
    必须调用 :func:`run_profile_backtest_from_pit_snapshot`。
    """

    profile = load_trading_profile(profile_id)
    raw_market_df = load_profile_market_data(profile)
    source_manifest = _load_safe_source_manifest(profile)
    return _run_profile_backtest_with_input(
        profile=profile,
        raw_market_df=raw_market_df,
        source_manifest=source_manifest,
        strategy_ids=strategy_ids,
    )


def run_profile_backtest_from_pit_snapshot(
    profile_id: str | None = None,
    *,
    market_snapshot: MarketDataSnapshot,
    pit_selector: MarketDataPITSelector,
    strategy_ids: Sequence[str] | None = None,
) -> BacktestRun:
    """以明确 immutable PIT snapshot 运行一次可复现的静态研究回放。

    ``market_snapshot`` 必须由 ``MarketDataPITSelector.select(..., as_of=...)`` 从已校验的
    immutable ``DatasetVersion`` 构造，并由同一 ``pit_selector`` 在入口重新计算验证；此入口
    不会回退到 legacy 路径、当前时钟或数据集 ``latest``。快照身份会写入 run manifest，
    因此之后发布的修订不会改变已归档回测。

    这是单一 as-of 数据视图的回放，不是逐决策时点的完整历史模拟。需要严格的每步
    look-ahead guard 时，调用方必须为每个 simulation time 构造相应 snapshot；不得把
    一次回测结束时的静态 snapshot 声称为所有历史时点均已可见的数据。
    """

    if not isinstance(market_snapshot, MarketDataSnapshot):
        raise ValueError("market_snapshot 必须是 MarketDataSnapshot")
    if not isinstance(pit_selector, MarketDataPITSelector):
        raise ValueError("pit_selector 必须是 MarketDataPITSelector")
    verified_snapshot = pit_selector.select(
        dataset_version_hash=market_snapshot.dataset_version_hash,
        spec=market_snapshot.spec,
        as_of=market_snapshot.as_of,
    )
    if verified_snapshot.snapshot_id != market_snapshot.snapshot_id:
        raise ValueError("PIT snapshot 未能通过 immutable DatasetVersion 重算验证")
    profile = load_trading_profile(profile_id)
    validate_profile_data_governance(profile)
    raw_market_df = verified_snapshot.selected_frame()
    _validate_pit_snapshot_for_profile(profile, verified_snapshot, raw_market_df)
    validate_market_dataset(profile, raw_market_df)
    return _run_profile_backtest_with_input(
        profile=profile,
        raw_market_df=raw_market_df,
        source_manifest=_pit_source_manifest(profile, verified_snapshot, raw_market_df),
        strategy_ids=strategy_ids,
    )


def _run_profile_backtest_with_input(
    *,
    profile: TradingProfile,
    raw_market_df: pl.DataFrame,
    source_manifest: dict[str, object],
    strategy_ids: Sequence[str] | None,
) -> BacktestRun:
    """以已经选定并校验的数据输入执行唯一回测编排。"""

    signal_market_df = to_signal_market_data(profile, raw_market_df)
    selected_strategy_ids = resolve_selected_profile_strategy_ids(profile, strategy_ids)
    pipeline = run_profile_strategy_pipeline(
        signal_market_df,
        profile,
        strategy_ids=selected_strategy_ids if strategy_ids is not None else None,
        latest_only=False,
    )
    if pipeline.output_type != StrategyOutputType.TARGET_WEIGHT:
        raise ValueError(
            f"策略输出类型为 {pipeline.output_type.value}，不能运行目标权重回测；"
            "当前正式回测器仅接受 target_weight。"
        )
    if pipeline.frame.is_empty():
        raise ValueError("策略管线没有产生任何历史目标权重，已拒绝生成空回测报告")

    backtester = resolve_target_backtester(profile)
    request = _build_backtest_request(
        profile=profile,
        source_manifest=source_manifest,
        selected_strategy_ids=selected_strategy_ids,
        pipeline=pipeline,
    )
    result = run_target_backtest(profile, raw_market_df, pipeline.frame)
    if result.engine is not request.engine:
        raise ValueError(
            "回测器返回的引擎语义与画像 engine 不一致，已拒绝生成不可信结果："
            f"{result.engine.value} != {request.engine.value}"
        )
    result = result.bind_request(request)
    if not result.equity_curve:
        raise ValueError("回测器未返回净值曲线，已拒绝生成不可审计的回测结果")

    latest_holdings = latest_pipeline_output(pipeline)
    periods_per_year = _result_periods_per_year(profile)
    evaluation = _build_evaluation_view(
        result,
        targets=pipeline.frame,
        time_column=pipeline.time_column,
        execution_delay_sessions=profile.backtest.execution_delay_sessions,
    )
    evaluation_curve = _report_equity_curve(evaluation)
    performance = _calculate_equity_performance(
        evaluation_curve,
        periods_per_year=periods_per_year,
    )
    benchmark = _build_benchmark_analytics(
        signal_market_df,
        benchmark_symbol=profile.benchmark_symbol,
        equity_curve=evaluation_curve,
        periods_per_year=periods_per_year,
        sample_is_sufficient=bool(performance["sample_is_sufficient"]),
    )
    execution = _build_execution_analytics(result)
    admission = evaluate_research_admission(
        profile,
        source_manifest=source_manifest,
        raw_market_df=raw_market_df,
        equity_curve=evaluation_curve,
        performance=performance,
        execution=execution,
    ).to_dict()
    analytics: dict[str, object] = {
        "equity_curve": evaluation_curve,
        "drawdown_curve": evaluation["drawdown_curve"],
        "monthly_returns": evaluation["monthly_returns"],
        "turnover_curve": evaluation["turnover_curve"],
        "full_equity_curve": [dict(row) for row in result.equity_curve],
        "full_drawdown_curve": [dict(row) for row in result.drawdown_curve],
        "evaluation": evaluation["metadata"],
        "trades": [dict(row) for row in result.trades],
        "orders": [dict(row) for row in result.orders],
        "rejected_orders": result.rejected_orders,
        "performance": performance,
        "benchmark": benchmark,
        "execution": execution,
        "admission": admission,
    }
    metrics = _build_report_metrics(
        result,
        performance=performance,
        benchmark=benchmark,
        execution=execution,
        backtester_id=backtester.backtester_id,
        evaluation=evaluation["metadata"],
        admission=admission,
    )
    manifest = _build_run_manifest(
        request=request,
        result=result,
        analytics=analytics,
        metrics=metrics,
        admission=admission,
    )
    return BacktestRun(
        profile=profile,
        selected_strategy_ids=selected_strategy_ids,
        backtester_id=backtester.backtester_id,
        pipeline=pipeline,
        latest_holdings=latest_holdings,
        result=result,
        metrics=metrics,
        analytics=analytics,
        manifest=manifest,
    )


def _build_backtest_request(
    *,
    profile: TradingProfile,
    source_manifest: Mapping[str, object],
    selected_strategy_ids: tuple[str, ...],
    pipeline: StrategyOutputBundle,
) -> BacktestRequest:
    """在唯一编排入口冻结引擎、目标、数据、成本与代码身份。

    运行时的行情 DataFrame 仍只传给相应 adapter；它不能进入 request/manifest，也不能
    以任意 dict 混入订单或券商对象。当前正式路径仅接受 target_weight。
    """

    if pipeline.output_type is not StrategyOutputType.TARGET_WEIGHT:
        raise ValueError("统一 BacktestRequest 当前仅支持 target_weight 策略输出")
    config = profile.backtest
    return BacktestRequest(
        engine=BacktestEngine.parse(config.engine),
        profile_id=profile.profile_id,
        profile_config_sha256=profile_config_sha256(profile),
        profile_dimension_key=profile.dimension_key,
        source_frequency=profile.data_frequency.value,
        signal_frequency=profile.strategy_data_frequency.value,
        execution_frequency=(
            "1m" if config.engine == BacktestEngine.FUTURES_INTRADAY_REPLAY.value else "1d"
        ),
        settlement_frequency="1d_eod",
        result_frequency="1d_eod",
        selected_strategy_ids=selected_strategy_ids,
        target=TargetFrameReference.from_frame(
            pipeline.frame,
            time_column=pipeline.time_column,
        ),
        data=BacktestDataReference.from_source_manifest(source_manifest),
        assumptions=BacktestAssumptions(
            initial_cash=config.initial_cash,
            commission_bps=config.commission_bps,
            min_commission=config.min_commission,
            slippage_bps=config.slippage_bps,
            slippage_ticks=config.slippage_ticks,
            max_volume_participation=config.max_volume_participation,
            lot_size=config.lot_size,
            execution_delay_sessions=config.execution_delay_sessions,
            sellable_after_sessions=config.sellable_after_sessions,
            order_ttl_bars=config.order_ttl_bars,
            queue_ahead_ratio=config.queue_ahead_ratio,
        ),
        code=BacktestCodeReference.from_mapping(_source_control_metadata()),
    )


def run_profile_backtest(profile_id: str | None = None) -> dict[str, Any]:
    """保留研究摘要 API，并委托给唯一的完整回测工作流。"""

    run = run_profile_backtest_run(profile_id)
    admission = run.analytics.get("admission")
    return {
        "profile_id": run.profile.profile_id,
        "run_id": run.run_id,
        "price_field": run.profile.data.price_field,
        "output_type": run.pipeline.output_type.value,
        "selected_strategy_ids": list(run.selected_strategy_ids),
        "total_return": run.result.total_return,
        "annualized_return": run.result.annualized_return,
        "max_drawdown": run.result.max_drawdown,
        "turnover_estimate": run.result.turnover_estimate,
        "symbols": sorted(set(run.latest_holdings["symbol"].to_list()))
        if "symbol" in run.latest_holdings.columns
        else [],
        "latest_holdings": run.latest_holdings.to_dicts(),
        "trade_count": len(run.result.trades),
        "rejected_order_count": len(run.result.rejected_orders),
        "research_admission_status": (
            admission.get("status") if isinstance(admission, Mapping) else None
        ),
    }


def _load_safe_source_manifest(profile: TradingProfile) -> dict[str, object]:
    """从已校验的数据 manifest 提取不含环境路径和下载参数的归档字段。"""

    manifest_path = dataset_manifest_path(profile_market_data_path(profile))
    raw = load_json(manifest_path)
    schema = raw.get("schema")
    return {
        "manifest_version": raw.get("manifest_version"),
        "dataset_id": raw.get("dataset_id"),
        "data_source": raw.get("data_source"),
        "content_sha256": raw.get("content_sha256"),
        "profile_config_sha256": raw.get("profile_config_sha256"),
        "schema_version": schema.get("schema_version") if isinstance(schema, dict) else None,
        "schema": schema if isinstance(schema, dict) else None,
        "row_count": raw.get("row_count"),
        "symbol_count": raw.get("symbol_count"),
        "symbols": raw.get("symbols"),
        "columns": raw.get("columns"),
        "start": raw.get("start"),
        "end": raw.get("end"),
        "quality": raw.get("quality"),
        "versions": raw.get("versions"),
        "governance": raw.get("governance"),
        "ingestion": raw.get("ingestion"),
        "point_in_time": {
            "status": "LEGACY_NOT_PIT",
            "reason": "data_manifest_v3 不含行级 available_at/revision 快照。",
        },
    }


def _validate_pit_snapshot_for_profile(
    profile: TradingProfile,
    market_snapshot: MarketDataSnapshot,
    raw_market_df: pl.DataFrame | None = None,
) -> None:
    """阻止把其他数据集、来源或 schema 的 PIT snapshot 送进当前画像。"""

    if market_snapshot.dataset_id != profile.data.dataset_id:
        raise ValueError(
            "PIT snapshot.dataset_id 与画像 data.dataset_id 不一致，已拒绝跨数据集回测"
        )
    if profile.data.source_id is None or market_snapshot.source_id != profile.data.source_id:
        raise ValueError(
            "PIT snapshot.source_id 与画像 data.source_id 不一致，已拒绝跨来源回测"
        )
    scope = market_snapshot.publication_scope
    if scope.purpose is not PublicationPurpose.HISTORICAL_BACKTEST:
        raise ValueError(
            "PIT snapshot 的冻结 publication authorization 不包含 historical_backtest 用途，"
            "已拒绝历史回测"
        )
    if scope.market != profile.market.value:
        raise ValueError("PIT snapshot 授权 market 与画像不一致，已拒绝跨市场回测")
    if scope.asset_type != profile.asset_type.value:
        raise ValueError("PIT snapshot 授权 asset_type 与画像不一致，已拒绝跨资产回测")
    if scope.frequency != profile.data_frequency.value:
        raise ValueError("PIT snapshot 授权 frequency 与画像不一致，已拒绝跨频率回测")
    if profile.futures is not None:
        expected_actual_contract_data = not profile.futures.symbols_are_continuous
        if scope.actual_contract_data is not expected_actual_contract_data:
            raise ValueError(
                "PIT snapshot 授权的 actual_contract_data 与画像行情语义不一致，"
                "已拒绝回测"
            )
    if raw_market_df is not None:
        _assert_scope_covers_profile_input(profile, scope, raw_market_df)
    if market_snapshot.spec.schema_version == "":  # dataclass 已验证；保留编排边界的失败关闭。
        raise ValueError("PIT snapshot 缺少 schema_version")
    expected_schema_version = schema_version_for_profile(profile)
    if market_snapshot.spec.schema_version != expected_schema_version:
        raise ValueError(
            "PIT snapshot.schema_version 与画像要求的 schema_version 不一致，"
            "已拒绝跨 schema 回测"
        )


def _pit_source_manifest(
    profile: TradingProfile,
    market_snapshot: MarketDataSnapshot,
    raw_market_df: pl.DataFrame,
) -> dict[str, object]:
    """将 immutable PIT snapshot 转为可归档、无路径的回测数据清单。"""

    _validate_pit_snapshot_for_profile(profile, market_snapshot, raw_market_df)
    point_in_time = market_snapshot.as_manifest_mapping()
    return {
        "manifest_version": "northstar_market_pit_source_manifest_v1",
        "dataset_id": market_snapshot.dataset_id,
        "data_source": market_snapshot.source_id,
        "content_sha256": market_snapshot.selected_frame_hash,
        "profile_config_sha256": profile_config_sha256(profile),
        "schema_version": market_snapshot.spec.schema_version,
        "schema": {
            "schema_version": market_snapshot.spec.schema_version,
            "point_in_time": True,
        },
        "row_count": len(market_snapshot.revisions),
        "governance": {
            "source_id": market_snapshot.source_id,
            "source_config_sha256": market_snapshot.source_config_sha256,
        },
        "point_in_time": point_in_time,
    }


def _assert_scope_covers_profile_input(
    profile: TradingProfile,
    scope: PublicationScope,
    raw_market_df: pl.DataFrame,
) -> None:
    """把冻结授权的产品/交易所范围与实际回测输入逐项绑定。

    ``PublicationScope`` 不是数据集的装饰性标签。连续研究画像用固定 universe 的
    ``continuous_symbol`` 映射，实际合约画像要求规范化 ``product/exchange`` 列；任一未知
    标的、错误交易所或 scope 未覆盖的品种均不能进入回测。
    """

    scope_products = set(scope.products)
    scope_exchanges = set(scope.exchanges)
    if not scope_products or not scope_exchanges:
        raise ValueError("PIT snapshot publication scope 必须显式列出 products 与 exchanges")
    if not isinstance(raw_market_df, pl.DataFrame) or "symbol" not in raw_market_df.columns:
        raise ValueError("PIT 回测输入缺少 symbol，无法核对授权产品范围")

    universe = load_instrument_universe(profile.universe_id)
    members_by_continuous = {
        member.continuous_symbol: member for member in universe.members
    }
    members_by_product = {member.product: member for member in universe.members}

    observed_symbols = {
        str(symbol).strip().upper() for symbol in raw_market_df.get_column("symbol").unique().to_list()
    }
    if not observed_symbols or "" in observed_symbols:
        raise ValueError("PIT 回测输入包含空 symbol，无法核对授权产品范围")
    configured_symbols = {
        str(symbol).strip().upper() for symbol in profile.data.download.symbols
    }

    required_members: set[InstrumentUniverseMember]
    if profile.futures is not None and profile.futures.symbols_are_continuous:
        symbols = observed_symbols | configured_symbols
        unknown_symbols = sorted(symbols.difference(members_by_continuous))
        if unknown_symbols:
            raise ValueError(
                "PIT 回测输入含不属于画像品种池的连续标的：" + ", ".join(unknown_symbols)
            )
        required_members = {members_by_continuous[symbol] for symbol in symbols}
    else:
        if "product" not in raw_market_df.columns or "exchange" not in raw_market_df.columns:
            raise ValueError(
                "实际合约 PIT 回测输入必须包含 product 与 exchange，才能核对授权范围"
            )
        observed_pairs = {
            (str(product).strip().upper(), str(exchange).strip().upper())
            for product, exchange in raw_market_df.select("product", "exchange")
            .unique()
            .iter_rows()
        }
        if not observed_pairs or any(not product or not exchange for product, exchange in observed_pairs):
            raise ValueError("实际合约 PIT 回测输入包含空 product/exchange")
        configured_products = configured_symbols
        unknown_products = sorted(
            configured_products.union(product for product, _ in observed_pairs).difference(members_by_product)
        )
        if unknown_products:
            raise ValueError(
                "PIT 回测输入含不属于画像品种池的实际品种：" + ", ".join(unknown_products)
            )
        wrong_exchanges = sorted(
            product
            for product, exchange in observed_pairs
            if members_by_product[product].exchange != exchange
        )
        if wrong_exchanges:
            raise ValueError(
                "PIT 回测输入的实际品种交易所与画像品种池不一致："
                + ", ".join(wrong_exchanges)
            )
        required_members = {
            members_by_product[product]
            for product in configured_products.union(product for product, _ in observed_pairs)
        }

    required_products = {member.product for member in required_members}
    required_exchanges = {member.exchange for member in required_members}
    missing_products = sorted(required_products.difference(scope_products))
    missing_exchanges = sorted(required_exchanges.difference(scope_exchanges))
    if missing_products or missing_exchanges:
        details: list[str] = []
        if missing_products:
            details.append("products 缺少：" + ", ".join(missing_products))
        if missing_exchanges:
            details.append("exchanges 缺少：" + ", ".join(missing_exchanges))
        raise ValueError("PIT snapshot publication scope 未覆盖画像回测输入；" + "；".join(details))


def _result_periods_per_year(profile: TradingProfile) -> int:
    """返回净值曲线实际采样频率的年化周期数。

    分钟订单回放虽读取分钟 bar，但对外输出的是日终权益曲线，风险统计必须按 252 个
    交易日年化，不能误用分钟 bar 的 98,280 周期。
    """

    if profile.backtest.engine in {"futures_daily", "futures_intraday_replay"}:
        return 252
    return periods_per_year_for_frequency(profile.data_frequency)


def _build_evaluation_view(
    result: BacktestResult,
    *,
    targets: pl.DataFrame,
    time_column: str,
    execution_delay_sessions: int,
) -> dict[str, object]:
    """剔除信号热身期，并以首次可执行非零目标前的权益作为评估基线。

    策略的 lookback 期间没有可交易信号，若把这段零暴露直接混进年化样本，会让短样本
    的收益、波动和比率看起来比实际更稳定。原始完整曲线仍保留在 ``BacktestResult``
    和报告的 ``full_equity_curve`` 中，供审计追溯。
    """

    full_curve = result.equity_curve
    points = _normalized_equity_points(full_curve)
    target_start = _first_nonzero_target_date(targets, time_column=time_column)
    metadata: dict[str, object] = {
        "source_equity_observation_count": len(points),
        "first_nonzero_target_date": target_start,
        "execution_delay_sessions": execution_delay_sessions,
    }
    if target_start is None:
        normalized_curve = [dict(row) for row in full_curve]
        metadata.update(
            {
                "status": "no_active_target",
                "evaluation_start": str(normalized_curve[0]["date"])[:10],
                "warmup_excluded_observation_count": 0,
                "evaluation_observation_count": len(normalized_curve),
                "note": "回测期间没有非零目标权重；绩效仅表示空仓基线。",
            }
        )
        return _evaluation_payload(result, normalized_curve, metadata)

    decision_index = next(
        (
            index
            for index, (point_date, _) in enumerate(points)
            if point_date >= target_start
        ),
        None,
    )
    if decision_index is None:
        raise ValueError("首个非零目标不在回测净值曲线范围内")
    evaluation_index = decision_index + execution_delay_sessions
    if evaluation_index >= len(points):
        normalized_curve = [dict(row) for row in full_curve]
        metadata.update(
            {
                "status": "no_executable_target",
                "evaluation_start": str(normalized_curve[0]["date"])[:10],
                "warmup_excluded_observation_count": 0,
                "evaluation_observation_count": len(normalized_curve),
                "note": "非零目标出现得过晚，样本中没有后续可执行交易日。",
            }
        )
        return _evaluation_payload(result, normalized_curve, metadata)

    baseline_equity = points[evaluation_index - 1][1] if evaluation_index > 0 else 1.0
    normalized_curve = []
    for row in full_curve[evaluation_index:]:
        normalized_row = dict(row)
        normalized_row["equity"] = _as_finite_float(
            row.get("equity"),
            default=math.nan,
        ) / baseline_equity
        normalized_curve.append(normalized_row)
    metadata.update(
        {
            "status": "active_target_evaluation",
            "evaluation_start": str(normalized_curve[0]["date"])[:10],
            "baseline_date": points[evaluation_index - 1][0]
            if evaluation_index > 0
            else None,
            "warmup_excluded_observation_count": evaluation_index,
            "evaluation_observation_count": len(normalized_curve),
        }
    )
    return _evaluation_payload(result, normalized_curve, metadata)


def _evaluation_payload(
    result: BacktestResult,
    equity_curve: Sequence[Mapping[str, object]],
    metadata: dict[str, object],
) -> dict[str, object]:
    """从评估权益切片重建回撤、月度收益和同期换手曲线。"""

    normalized_equity_curve = [dict(row) for row in equity_curve]
    points = _normalized_equity_points(normalized_equity_curve)
    returns = _returns_from_normalized_equities([equity for _, equity in points])
    peak = 1.0
    drawdown_curve: list[dict[str, float | str]] = []
    for (point_date, equity) in points:
        peak = max(peak, equity)
        drawdown_curve.append({"date": point_date, "drawdown": equity / peak - 1.0})
    monthly_products: dict[str, float] = {}
    for (point_date, _), value in zip(points, returns, strict=True):
        month = point_date[:7]
        monthly_products[month] = monthly_products.get(month, 1.0) * (1.0 + value)
    monthly_returns = [
        {"month": month, "return": product - 1.0}
        for month, product in sorted(monthly_products.items())
    ]
    start_date = points[0][0]
    turnover_curve = [
        dict(row)
        for row in result.turnover_curve
        if str(row.get("date") or "")[:10] >= start_date
    ]
    return {
        "equity_curve": normalized_equity_curve,
        "drawdown_curve": drawdown_curve,
        "monthly_returns": monthly_returns,
        "turnover_curve": turnover_curve,
        "metadata": metadata,
    }


def _first_nonzero_target_date(
    targets: pl.DataFrame,
    *,
    time_column: str,
) -> str | None:
    required = {time_column, "target_weight"}
    if not required.issubset(targets.columns):
        raise ValueError("策略目标缺少评估期所需的时间列或 target_weight")
    active = targets.filter(pl.col("target_weight").abs() > _EPSILON)
    if active.is_empty():
        return None
    value = active.select(pl.col(time_column).min()).item()
    return str(value)[:10]


def _report_equity_curve(payload: dict[str, object]) -> list[dict[str, object]]:
    curve = payload.get("equity_curve")
    if not isinstance(curve, list):
        raise ValueError("回测运行缺少评估净值曲线")
    rows = [row for row in curve if isinstance(row, dict)]
    if len(rows) != len(curve):
        raise ValueError("评估净值曲线包含无效记录")
    return rows


def _calculate_equity_performance(
    equity_curve: Sequence[Mapping[str, object]],
    *,
    periods_per_year: int,
) -> dict[str, object]:
    """从日终归一化权益计算收益、风险与回撤指标。

    首期收益以初始权益 1.0 为基准，避免首日亏损被忽略。无风险利率固定为 0，并写入
    输出，防止把 Sharpe/Sortino 的假设藏在实现细节里。
    """

    if periods_per_year <= 0:
        raise ValueError("年化周期数必须大于 0")
    points = _normalized_equity_points(equity_curve)
    if not points:
        raise ValueError("净值曲线不能为空")
    equities = [equity for _, equity in points]
    returns: list[float] = []
    previous = 1.0
    peak = 1.0
    drawdowns: list[float] = []
    for equity in equities:
        returns.append(equity / previous - 1.0)
        previous = equity
        peak = max(peak, equity)
        drawdowns.append(equity / peak - 1.0)

    total_return = equities[-1] - 1.0
    sample_is_sufficient = len(returns) >= _MIN_STATISTICAL_OBSERVATIONS
    annualized_return = (
        (
            -1.0
            if equities[-1] <= 0
            else equities[-1] ** (periods_per_year / len(equities)) - 1.0
        )
        if sample_is_sufficient
        else None
    )
    volatility = _sample_standard_deviation(returns)
    annualized_volatility = (
        volatility * math.sqrt(periods_per_year)
        if sample_is_sufficient and volatility is not None
        else None
    )
    average_return = sum(returns) / len(returns)
    sharpe_ratio = (
        average_return / volatility * math.sqrt(periods_per_year)
        if sample_is_sufficient and volatility is not None and volatility > _EPSILON
        else None
    )
    downside_deviation = math.sqrt(
        sum(min(value, 0.0) ** 2 for value in returns) / len(returns)
    )
    sortino_ratio = (
        average_return / downside_deviation * math.sqrt(periods_per_year)
        if sample_is_sufficient and downside_deviation > _EPSILON
        else None
    )
    max_drawdown = min(drawdowns, default=0.0)
    calmar_ratio = (
        annualized_return / abs(max_drawdown)
        if annualized_return is not None and max_drawdown < -_EPSILON
        else None
    )
    return {
        "return_observation_count": len(returns),
        "minimum_statistical_observation_count": _MIN_STATISTICAL_OBSERVATIONS,
        "sample_is_sufficient": sample_is_sufficient,
        "sample_status": (
            "统计样本充足"
            if sample_is_sufficient
            else f"样本不足（至少需要 {_MIN_STATISTICAL_OBSERVATIONS} 个执行后权益观测）"
        ),
        "periods_per_year": periods_per_year,
        "annual_risk_free_rate": 0.0,
        "total_return": total_return,
        "annualized_return": annualized_return,
        "annualized_volatility": annualized_volatility,
        "sharpe_ratio": sharpe_ratio,
        "sortino_ratio": sortino_ratio,
        "max_drawdown": max_drawdown,
        "calmar_ratio": calmar_ratio,
        "positive_return_ratio": sum(value > 0.0 for value in returns) / len(returns),
    }


def _build_benchmark_analytics(
    signal_market_df: pl.DataFrame,
    *,
    benchmark_symbol: str,
    equity_curve: Sequence[Mapping[str, object]],
    periods_per_year: int,
    sample_is_sufficient: bool = True,
) -> dict[str, object]:
    """以同一信号价格口径构造基准曲线；缺失时明确标注不可计算。"""

    required = {"date", "symbol", "close"}
    if not required.issubset(signal_market_df.columns):
        return {
            "status": "unavailable",
            "symbol": benchmark_symbol,
            "reason": "信号行情缺少 date/symbol/close，无法计算基准",
        }
    benchmark_rows = signal_market_df.filter(pl.col("symbol") == benchmark_symbol)
    if benchmark_rows.is_empty():
        return {
            "status": "unavailable",
            "symbol": benchmark_symbol,
            "reason": "数据集不含画像指定的基准标的",
        }
    duplicate_dates = benchmark_rows.group_by("date").len().filter(pl.col("len") != 1)
    if not duplicate_dates.is_empty():
        return {
            "status": "unavailable",
            "symbol": benchmark_symbol,
            "reason": "基准价格在同一日期存在重复记录",
        }

    prices = {
        str(row["date"])[:10]: float(row["close"])
        for row in benchmark_rows.select("date", "close").to_dicts()
        if _is_finite_positive(row["close"])
    }
    points = _normalized_equity_points(equity_curve)
    missing_dates = [point_date for point_date, _ in points if point_date not in prices]
    if missing_dates:
        return {
            "status": "unavailable",
            "symbol": benchmark_symbol,
            "reason": "回测净值日期缺少基准价格：" + ", ".join(missing_dates[:3]),
        }
    base_price = prices[points[0][0]]
    if not _is_finite_positive(base_price):
        return {
            "status": "unavailable",
            "symbol": benchmark_symbol,
            "reason": "基准起始价格无效",
        }

    benchmark_equities = [prices[point_date] / base_price for point_date, _ in points]
    strategy_equities = [equity for _, equity in points]
    strategy_returns = _returns_from_normalized_equities(strategy_equities)
    benchmark_returns = _returns_from_normalized_equities(benchmark_equities)
    active_returns = [
        strategy_return - benchmark_return
        for strategy_return, benchmark_return in zip(
            strategy_returns,
            benchmark_returns,
            strict=True,
        )
    ]
    tracking_error = _sample_standard_deviation(active_returns)
    information_ratio = (
        (sum(active_returns) / len(active_returns))
        / tracking_error
        * math.sqrt(periods_per_year)
        if sample_is_sufficient
        and tracking_error is not None
        and tracking_error > _EPSILON
        else None
    )
    benchmark_total_return = benchmark_equities[-1] - 1.0
    benchmark_annualized_return = (
        -1.0
        if benchmark_equities[-1] <= 0
        else benchmark_equities[-1] ** (periods_per_year / len(benchmark_equities)) - 1.0
    ) if sample_is_sufficient else None
    return {
        "status": "available",
        "symbol": benchmark_symbol,
        "total_return": benchmark_total_return,
        "annualized_return": benchmark_annualized_return,
        "excess_total_return": strategy_equities[-1] - benchmark_equities[-1],
        "tracking_error": (
            tracking_error * math.sqrt(periods_per_year)
            if sample_is_sufficient and tracking_error is not None
            else None
        ),
        "information_ratio": information_ratio,
        "sample_is_sufficient": sample_is_sufficient,
        "equity_curve": [
            {"date": point_date, "equity": equity}
            for (point_date, _), equity in zip(points, benchmark_equities, strict=True)
        ],
    }


def _build_execution_analytics(
    result: BacktestResult,
) -> dict[str, object]:
    """从统一结果的 tagged audit 投影报告字段。

    这里不再按 ``backtester_id`` 字符串猜测能力；真实性由 ``BacktestResult.engine``
    的固定 semantics 决定。fill 始终是模拟成交事件，不是已闭合交易。
    """

    audit = result.execution_audit
    if audit.level.value == "not_modeled":
        return {
            "detail_level": "not_modeled",
            "message": "连续收益研究引擎未模拟逐笔订单、成交、保证金或成交约束。",
            "limitations": list(result.limitations),
        }

    payload = audit.as_mapping()
    payload["detail_level"] = (
        "orders_and_fills"
        if audit.level.value == "orders_and_fill_events"
        else "fills_and_target_events"
    )
    reasons = [str(row.get("reason") or "unknown") for row in result.trades]
    payload["fill_reason_counts"] = {
        reason: reasons.count(reason) for reason in sorted(set(reasons))
    }
    margin_ratios = _finite_curve_values(result.equity_curve, "margin_ratio")
    available_funds_ratios = _finite_curve_values(
        result.equity_curve,
        "available_funds_ratio",
    )
    if margin_ratios:
        payload["max_margin_ratio"] = max(margin_ratios)
    if available_funds_ratios:
        payload["min_available_funds_ratio"] = min(available_funds_ratios)
    if audit.level.value == "orders_and_fill_events":
        status_counts = dict(audit.order_status_counts)
        payload.update(
            {
                "order_count": audit.order_event_count,
                "order_status_counts": status_counts,
                "partial_fill_order_count": sum(
                    0 < _as_finite_float(row.get("filled_qty"), default=0.0)
                    < _as_finite_float(row.get("requested_qty"), default=0.0)
                    for row in result.orders
                ),
                "rejected_order_count": audit.rejected_event_count,
            }
        )
    else:
        payload["target_constraint_event_count"] = len(result.rejected_orders)
        payload["target_constraint_events"] = list(result.rejected_orders)
    return payload


def _build_report_metrics(
    result: BacktestResult,
    *,
    performance: dict[str, object],
    benchmark: dict[str, object],
    execution: dict[str, object],
    backtester_id: str,
    evaluation: object,
    admission: Mapping[str, object],
) -> dict[str, object]:
    """构造报告展示指标，并在键名中保留口径与不可用状态。"""

    metrics: dict[str, object] = {
        "回测器": backtester_id,
        "总收益率": performance["total_return"],
        "年化收益率": performance["annualized_return"],
        "年化波动率": performance["annualized_volatility"],
        "夏普比率（无风险利率=0）": performance["sharpe_ratio"],
        "索提诺比率（无风险利率=0）": performance["sortino_ratio"],
        "最大回撤": performance["max_drawdown"],
        "卡玛比率": performance["calmar_ratio"],
        "正收益期占比": performance["positive_return_ratio"],
        "权益收益观测数": performance["return_observation_count"],
        "统计样本状态": performance["sample_status"],
        "最小统计观测数": performance["minimum_statistical_observation_count"],
        "年化周期数": performance["periods_per_year"],
        "平均换手（引擎口径）": result.turnover_estimate,
    }
    if isinstance(evaluation, dict):
        metrics["评估起始日"] = evaluation.get("evaluation_start")
        metrics["热身排除权益观测数"] = evaluation.get(
            "warmup_excluded_observation_count"
        )
    metrics["研究准入结论"] = admission.get("status")
    metrics["研究准入政策"] = admission.get("policy_id")
    metrics["研究准入阻断项"] = admission.get("blocking_check_count")
    if benchmark.get("status") == "available":
        metrics.update(
            {
                "基准总收益率": benchmark.get("total_return"),
                "相对基准总超额收益": benchmark.get("excess_total_return"),
                "跟踪误差": benchmark.get("tracking_error"),
                "信息比率": benchmark.get("information_ratio"),
            }
        )
    else:
        metrics["基准比较"] = "N/A（" + str(benchmark.get("reason") or "不可计算") + "）"

    detail_level = str(execution.get("detail_level") or "not_modeled")
    if detail_level == "not_modeled":
        metrics["成交与订单模拟"] = "未建模（连续收益研究）"
    else:
        metrics["成交事件数"] = execution.get("fill_event_count")
        metrics["累计成交数量"] = execution.get("filled_quantity")
        metrics["累计手续费"] = execution.get("commission_total")
        metrics["累计成交名义金额"] = execution.get("traded_notional_total")
        if detail_level == "orders_and_fills":
            metrics["订单事件数"] = execution.get("order_count")
            metrics["拒绝订单数"] = execution.get("rejected_order_count")
        else:
            metrics["目标约束/未成交事件数"] = execution.get(
                "target_constraint_event_count"
            )
        if "max_margin_ratio" in execution:
            metrics["最大保证金/权益"] = execution.get("max_margin_ratio")
        if "min_available_funds_ratio" in execution:
            metrics["最低可用资金/权益"] = execution.get(
                "min_available_funds_ratio"
            )
    return metrics


def _build_run_manifest(
    *,
    request: BacktestRequest,
    result: BacktestResult,
    analytics: Mapping[str, object],
    metrics: Mapping[str, object],
    admission: Mapping[str, object],
) -> RunManifest:
    """建立 v4 typed 清单；结果、分析和指标 hash 都在报告前复验。"""

    return RunManifest.create(
        request=request,
        result=result,
        analytics=analytics,
        metrics=metrics,
        admission=admission,
    )


def _normalized_equity_points(
    equity_curve: Sequence[Mapping[str, object]],
) -> list[tuple[str, float]]:
    points: list[tuple[str, float]] = []
    for row in equity_curve:
        point_date = str(row.get("date") or "")[:10]
        equity = _as_finite_float(row.get("equity"), default=math.nan)
        if not point_date or not math.isfinite(equity) or equity <= 0:
            raise ValueError("净值曲线包含空日期、非有限值或非正权益")
        points.append((point_date, equity))
    points.sort(key=lambda item: item[0])
    dates = [point_date for point_date, _ in points]
    if len(set(dates)) != len(dates):
        raise ValueError("净值曲线包含重复日期，无法计算可靠绩效")
    return points


def _returns_from_normalized_equities(equities: list[float]) -> list[float]:
    if not equities:
        return []
    returns: list[float] = []
    previous = 1.0
    for equity in equities:
        returns.append(equity / previous - 1.0)
        previous = equity
    return returns


def _sample_standard_deviation(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _finite_curve_values(
    curve: Sequence[Mapping[str, object]],
    field_name: str,
) -> list[float]:
    values: list[float] = []
    for row in curve:
        if field_name not in row:
            continue
        value = _as_finite_float(row[field_name], default=math.nan)
        if math.isfinite(value):
            values.append(value)
    return values


def _as_finite_float(value: object, *, default: float) -> float:
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return numeric if math.isfinite(numeric) else default


def _is_finite_positive(value: object) -> bool:
    return _as_finite_float(value, default=math.nan) > 0.0


def _source_control_metadata() -> dict[str, object]:
    """读取当前代码身份；不可读取 Git 时不阻断离线研究。

    ``worktree_sha256`` 覆盖已跟踪差异，以及未跟踪的 ``src/``、``configs/`` 与
    ``pyproject.toml``。它不记录路径或源代码内容，只改变可复现性身份。
    """

    project_root = Path(__file__).resolve().parents[3]
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        diff = subprocess.run(
            ["git", "diff", "HEAD", "--binary", "--no-ext-diff"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=False,
            timeout=5,
        ).stdout
        untracked = subprocess.run(
            [
                "git",
                "ls-files",
                "--others",
                "--exclude-standard",
                "--",
                "src",
                "configs",
                "pyproject.toml",
            ],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.splitlines()
        return {
            "package_version": __version__,
            "git_commit": commit or None,
            "git_dirty": bool(status.strip()),
            "worktree_sha256": _working_tree_sha256(
                project_root=project_root,
                tracked_diff=diff,
                untracked_paths=untracked,
            ),
        }
    except (OSError, subprocess.SubprocessError):
        return {
            "package_version": __version__,
            "git_commit": None,
            "git_dirty": None,
            "worktree_sha256": None,
        }


def _working_tree_sha256(
    *,
    project_root: Path,
    tracked_diff: bytes,
    untracked_paths: Sequence[str],
) -> str:
    """返回受控工作树身份；拒绝把文件内容或路径写入清单。"""

    digest = hashlib.sha256()
    digest.update(b"northstar-backtest-worktree-v1\0tracked-diff\0")
    digest.update(tracked_diff)
    for raw_relative_path in sorted(untracked_paths):
        relative_path = Path(raw_relative_path)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise OSError("Git 返回了不安全的未跟踪路径")
        candidate = project_root / relative_path
        if candidate.is_symlink() or not candidate.is_file():
            raise OSError("未跟踪运行时代码路径不是普通文件")
        digest.update(b"\0untracked-runtime-file\0")
        digest.update(relative_path.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(candidate.read_bytes())
    return digest.hexdigest()
