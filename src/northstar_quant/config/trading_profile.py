"""交易画像加载与解析。"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar, cast

from northstar_quant.common.enums import (
    AssetType,
    DataFrequency,
    Market,
    RebalanceFrequency,
    StrategyFamily,
    StringEnum,
)
from northstar_quant.common.types import TradingDimensions
from northstar_quant.config.settings import get_settings
from northstar_quant.config.yaml_loader import load_yaml


EnumValue = TypeVar("EnumValue", bound=StringEnum)


def _parse_enum(enum_cls: type[EnumValue], value: str | EnumValue) -> EnumValue:
    if isinstance(value, enum_cls):
        return value
    return cast(EnumValue, enum_cls.parse(str(value)))


def _parse_bool(value: object, *, field_name: str) -> bool:
    """严格解析配置布尔值，避免字符串 ``"false"`` 被当成真值。"""

    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ValueError(f"配置字段 {field_name} 必须是明确的布尔值")


@dataclass(frozen=True, slots=True)
class ProfileDownloadConfig:
    """交易画像中的数据下载配置；只描述如何取得数据，不代表数据已经可信。"""

    enabled: bool = False  # 是否允许 ``data download`` 为该画像发起自动下载。
    provider: str = "akshare"  # 下载提供器；当前只支持国内期货连续合约的 akshare。
    symbols: tuple[str, ...] = ()  # 下载标的池，使用数据源可识别的符号格式。
    start_date: str | None = None  # 下载起始日期，YYYY-MM-DD；为空时由提供器决定。
    end_date: str | None = None  # 下载结束日期，YYYY-MM-DD；为空通常下载至最新可得日期。
    options: dict[str, Any] = field(default_factory=dict)  # 仅传给对应提供器的扩展参数。


@dataclass(frozen=True, slots=True)
class ProfileDataConfig:
    """交易画像中的标准数据集、价格口径和真实交易资格。"""

    provider: str = "akshare"  # 标准数据集的来源标识；当前只支持 akshare。
    dataset_id: str = "core"  # 数据集逻辑版本/名称，用于 manifest 与画像匹配。
    path: str = ""  # 相对 storage/market 的标准化数据文件路径。
    price_field: str = "close"  # 策略和研究使用的价格列，如 close 或 adjusted_close。
    adjusted: bool = True  # 数据集是否按复权语义处理；不替代 price_field 的明确选择。
    live_trading_eligible: bool = False  # 真实交易数据资格开关，默认 false 且不能单独放行下单。
    download: ProfileDownloadConfig = field(default_factory=ProfileDownloadConfig)  # 数据取得规则。


@dataclass(frozen=True, slots=True)
class ProfileFuturesConfig:
    """期货画像的合约主数据与研究/执行边界。"""

    contract_spec_path: str = ""  # 相对项目根目录的期货合约规格文件路径。
    ctp_contract_mapping_path: str = ""  # 连续合约到 CTP 具体合约的显式映射文件。
    symbols_are_continuous: bool = True  # 行情 symbol 是否为连续合约；连续合约不能直接下单。
    execution_allowed: bool = False  # 仅实际可交易合约、完整规格和券商适配完成后才可设为 true。


@dataclass(frozen=True, slots=True)
class ProfileStrategyConfig:
    """交易画像中一条策略的启用状态、分类、资金权重和参数覆盖。"""

    strategy_id: str  # 策略注册表中的唯一 ID，不是策略展示名称。
    strategy_family: StrategyFamily | None = None  # 可选分类；提供时必须与注册表一致。
    capital_weight: float = 1.0  # 多策略组合前的资金权重；管线会按规则归一或校验。
    enabled: bool = True  # false 时保留配置但不参与策略管线。
    params: dict[str, Any] = field(default_factory=dict)  # 对策略构造器默认参数的显式覆盖。


@dataclass(frozen=True, slots=True)
class ProfileLifecycleConfig:
    """交易画像的运行角色；目录表达账户连接边界，角色表达安全门控。"""

    role: str = "experimental"  # offline 允许 research/experimental；simulated 为 simulated；live 为 production。
    line_id: str = "default"  # 同一策略线/账户线的稳定标识，用于运营和版本追踪。

    @property
    def is_production(self) -> bool:
        return self.role == "production"


@dataclass(frozen=True, slots=True)
class ProfileExecutionConfig:
    """交易画像中的组合再平衡与交易单位约束，不是券商订单本身。"""

    long_only: bool = True  # 是否只允许多头目标；不自动改变券商的做空权限。
    rebalance_min_trade_value: float | None = None  # 再平衡忽略的小额交易金额阈值。
    rebalance_weight_tolerance: float = 0.0  # 当前权重与目标权重差异小于此值时不交易。
    order_qty_step: float | None = None  # 所有方向通用的数量步长；可被买卖方向步长覆盖。
    buy_qty_step: float | None = None  # 开仓方向数量步长；期货通常为 1 手。
    sell_qty_step: float | None = None  # 平仓或开空方向数量步长；由期货柜台规则确定。


@dataclass(frozen=True, slots=True)
class ProfileBacktestConfig:
    """交易画像中的回测引擎与显式撮合假设；示例费率不能直接视为券商费率。"""

    engine: str = "weight_return"  # 连续合约研究回测器。
    initial_cash: float = 100_000.0  # 回测初始现金，不代表真实账户余额。
    commission_bps: float = 0.0  # 单边费率，单位为基点；1 bp = 0.01%。
    min_commission: float = 0.0  # 单笔最低佣金，计价货币与画像 currency 一致。
    slippage_bps: float = 0.0  # 成交滑点假设，单位为基点；用于保守模拟。
    lot_size: int = 1  # 撮合数量必须满足的最小整手单位。
    execution_delay_sessions: int = 1  # 信号产生后延迟多少个交易时段成交，至少为 1 防未来函数。
    sellable_after_sessions: int = 0  # 保留的通用撮合字段；期货 T+0 研究画像应为 0。


@dataclass(frozen=True, slots=True)
class ProfileVersionConfig:
    """可追溯性锚点；字段是版本标签，不会自动加载或比较外部配置。"""

    profile: str = "v1"  # 画像整体版本。
    benchmark: str = "v1"  # 基准定义及总回报口径版本。
    strategy_params: str = "v1"  # 策略参数集合版本。
    execution_policy: str = "v1"  # 成交、费用、交易单位等执行假设版本。
    risk_policy: str = "v1"  # 风控阈值和规则版本。


@dataclass(frozen=True, slots=True)
class TradingProfile:
    """统一描述一次完整交易流程；字段注释是 YAML 的运行时语义参考。"""

    profile_id: str  # 全局唯一 ID；必须等于文件名且带 _offline/_simulated/_live 后缀。
    name: str  # 面向人类的简短中文名称，不参与程序路由。
    market: Market  # 市场枚举：CN、US、HK；影响日历、规则和适配器选择。
    asset_type: AssetType  # 本项目固定为 FUTURES。
    data_frequency: DataFrequency  # 行情/信号频率，如 1d、1w、1m。
    rebalance_frequency: RebalanceFrequency  # 允许调整目标持仓的频率，不必等于数据频率。
    strategy_family: StrategyFamily  # 画像默认策略分类；具体策略可在 strategies 内覆盖。
    currency: str  # 计价货币，当前仅支持 CNY；费用和金额阈值均使用该货币。
    timezone: str  # IANA 时区，如 Asia/Shanghai；用于调度与报告日期边界。
    calendar: str  # exchange_calendars 日历 ID，如 XSHG、XNYS。
    universe_id: str  # 标的池/研究宇宙的稳定版本名称，不直接携带标的列表。
    benchmark_symbol: str  # 基准标的符号，用于报告和相对表现比较。
    data: ProfileDataConfig  # 行情来源、落盘位置、价格口径和下载规则。
    futures: ProfileFuturesConfig | None = None  # 仅 FUTURES 画像必填，声明连续合约与执行边界。
    strategies: tuple[ProfileStrategyConfig, ...] = ()  # 画像启用的策略清单与组合权重。
    lifecycle: ProfileLifecycleConfig = field(default_factory=ProfileLifecycleConfig)  # 连接边界与角色。
    execution: ProfileExecutionConfig = field(default_factory=ProfileExecutionConfig)  # 再平衡/手数执行约束。
    backtest: ProfileBacktestConfig = field(default_factory=ProfileBacktestConfig)  # 回测撮合假设。
    versions: ProfileVersionConfig = field(default_factory=ProfileVersionConfig)  # 可追溯性版本锚点。
    risk: dict[str, Any] = field(default_factory=dict)  # RiskLimits 支持字段的画像级覆盖，未知字段会拒绝。
    schedule: dict[str, Any] = field(default_factory=dict)  # cron 配置，如 rebalance_cron、daily_report_cron。
    metadata: dict[str, Any] = field(default_factory=dict)  # 未建模顶层字段；仅保留，不应假定影响交易行为。

    @property
    def enabled_strategies(self) -> tuple[ProfileStrategyConfig, ...]:
        return tuple(strategy for strategy in self.strategies if strategy.enabled)

    @property
    def dimensions(self) -> TradingDimensions:
        return TradingDimensions(
            market=self.market,
            asset_type=self.asset_type,
            data_frequency=self.data_frequency,
            rebalance_frequency=self.rebalance_frequency,
            strategy_family=self.strategy_family,
        )

    @property
    def dimension_key(self) -> str:
        return self.dimensions.key

    @property
    def is_production(self) -> bool:
        return self.lifecycle.is_production

    def strategy_dimensions(self, strategy: ProfileStrategyConfig) -> TradingDimensions:
        return TradingDimensions(
            market=self.market,
            asset_type=self.asset_type,
            data_frequency=self.data_frequency,
            rebalance_frequency=self.rebalance_frequency,
            strategy_family=strategy.strategy_family or self.strategy_family,
        )


def resolve_profile_id(profile_id: str | None = None) -> str:
    """解析交易画像 ID；为空时使用安全的全局默认画像。"""

    return profile_id or get_settings().default_profile_id


def get_profile_config_dir(config_dir: str | Path | None = None) -> Path:
    """返回交易画像配置目录。"""

    if config_dir is not None:
        path = Path(config_dir)
        if path.is_absolute():
            return path
        return get_settings().project_root / path
    return get_settings().profile_config_dir


_DIRECTORY_ALLOWED_ROLES = {
    "offline": frozenset({"research", "experimental"}),
    "simulated": frozenset({"simulated"}),
    "live": frozenset({"production"}),
}


def _discover_profile_paths(profile_dir: Path) -> dict[str, Path]:
    """递归发现交易画像，并以 YAML 内的 ``profile_id`` 建立唯一索引。"""

    if not profile_dir.exists():
        return {}

    paths_by_id: dict[str, Path] = {}
    for path in sorted(profile_dir.rglob("*.yaml")):
        raw = load_yaml(path)
        profile_id = str(raw.get("profile_id") or "").strip()
        if not profile_id:
            raise ValueError(f"交易画像配置缺少 profile_id：{path}")
        lifecycle_raw = raw.get("lifecycle", {}) or {}
        lifecycle_role = str(lifecycle_raw.get("role", "experimental")).strip().lower()
        _validate_profile_directory(
            path,
            profile_dir,
            profile_id=profile_id,
            lifecycle_role=lifecycle_role,
        )
        previous_path = paths_by_id.get(profile_id)
        if previous_path is not None:
            raise ValueError(
                f"交易画像 profile_id 重复：{profile_id}，"
                f"同时出现在 {previous_path} 和 {path}"
            )
        paths_by_id[profile_id] = path
    return paths_by_id


def get_profile_config_path(profile_id: str | None = None, config_dir: str | Path | None = None) -> Path:
    """按 YAML 内的 ``profile_id`` 返回对应的配置路径。"""

    resolved_profile_id = resolve_profile_id(profile_id)
    profile_dir = get_profile_config_dir(config_dir)
    return _discover_profile_paths(profile_dir).get(
        resolved_profile_id,
        profile_dir / f"{resolved_profile_id}.yaml",
    )


def list_trading_profiles(
    config_dir: str | Path | None = None,
    *,
    role: str | None = None,
) -> list[str]:
    """列出当前可用的交易画像 ID。"""

    profile_dir = get_profile_config_dir(config_dir)
    profiles = sorted(_discover_profile_paths(profile_dir))
    if role is None:
        return profiles
    normalized_role = str(role).strip().lower()
    return [
        profile_id
        for profile_id in profiles
        if load_trading_profile(profile_id, config_dir).lifecycle.role == normalized_role
    ]


def list_production_profiles(config_dir: str | Path | None = None) -> list[str]:
    """列出标记为 production 的交易画像。"""

    return list_trading_profiles(config_dir, role="production")


def get_production_profile_id(config_dir: str | Path | None = None) -> str:
    """返回唯一 production 画像；未配置时拒绝推断默认画像。"""

    production_profiles = list_production_profiles(config_dir)
    if len(production_profiles) == 1:
        return production_profiles[0]
    if len(production_profiles) > 1:
        joined = ", ".join(production_profiles)
        raise ValueError(f"当前存在多个 production 画像：{joined}")
    raise ValueError("当前没有标记为 production 的交易画像。")


def ensure_production_profile(profile: TradingProfile, *, context: str) -> TradingProfile:
    """确保给定画像可用于实盘主线。"""

    if profile.is_production:
        if profile.asset_type == AssetType.FUTURES:
            futures = profile.futures
            if futures is None or futures.symbols_are_continuous or not futures.execution_allowed:
                raise ValueError(
                    f"{context} 禁止使用连续合约或未明确授权的期货画像执行；"
                    "必须配置实际可交易合约及 futures.execution_allowed=true。"
                )
        return profile
    raise ValueError(
        f"{context} 仅允许使用 production 画像；当前 {profile.profile_id} 的角色为 {profile.lifecycle.role}。"
    )


def _validate_profile_directory(
    path: Path,
    profile_dir: Path,
    *,
    profile_id: str,
    lifecycle_role: str,
) -> None:
    """校验画像路径、ID、类别后缀与生命周期角色的一致性。"""

    relative_path = path.relative_to(profile_dir)
    if len(relative_path.parts) != 2:
        raise ValueError(
            f"交易画像 {profile_id} 必须直接位于类别目录中："
            f"{', '.join(sorted(_DIRECTORY_ALLOWED_ROLES))}"
        )
    directory_name = relative_path.parts[0]
    allowed_roles = _DIRECTORY_ALLOWED_ROLES.get(directory_name)
    if allowed_roles is None:
        raise ValueError(
            f"交易画像 {profile_id} 位于不支持的目录 {directory_name}；"
            f"仅支持：{', '.join(sorted(_DIRECTORY_ALLOWED_ROLES))}"
        )
    if lifecycle_role not in allowed_roles:
        raise ValueError(
            f"交易画像 {profile_id} 的 lifecycle.role={lifecycle_role} 不允许位于目录 "
            f"{directory_name}；允许角色：{', '.join(sorted(allowed_roles))}"
        )
    if path.stem != profile_id:
        raise ValueError(
            f"交易画像文件名必须与 profile_id 一致：{path.name} != {profile_id}.yaml"
        )
    required_suffix = f"_{directory_name}"
    if not profile_id.endswith(required_suffix):
        raise ValueError(
            f"交易画像 {profile_id} 必须以类别后缀 {required_suffix} 结尾"
        )


@lru_cache(maxsize=None)
def load_trading_profile(
    profile_id: str | None = None,
    config_dir: str | Path | None = None,
) -> TradingProfile:
    """从 YAML 读取交易画像。"""

    resolved_profile_id = resolve_profile_id(profile_id)
    path = get_profile_config_path(resolved_profile_id, config_dir)
    if not path.exists():
        available_profiles = ", ".join(list_trading_profiles(config_dir)) or "无"
        raise FileNotFoundError(
            f"交易画像配置不存在：{path}。当前可用画像：{available_profiles}"
        )

    raw = load_yaml(path)
    data_raw = raw.get("data", {}) or {}
    futures_raw = raw.get("futures")
    download_raw = data_raw.get("download", {}) or {}
    strategies_raw = raw.get("strategies", []) or []
    lifecycle_raw = raw.get("lifecycle", {}) or {}
    execution_raw = raw.get("execution", {}) or {}
    backtest_raw = raw.get("backtest", {}) or {}
    versions_raw = raw.get("versions", {}) or {}

    configured_profile_id = str(raw.get("profile_id") or "").strip()
    if configured_profile_id != resolved_profile_id:
        raise ValueError(
            f"交易画像路径索引与内容不一致：请求 {resolved_profile_id}，"
            f"配置声明 {configured_profile_id or '空'}"
        )
    lifecycle_role = str(lifecycle_raw.get("role", "experimental")).strip().lower()
    _validate_profile_directory(
        path,
        get_profile_config_dir(config_dir),
        profile_id=configured_profile_id,
        lifecycle_role=lifecycle_role,
    )

    market = _parse_enum(Market, raw.get("market", "CN"))
    asset_type = _parse_enum(AssetType, raw.get("asset_type", "FUTURES"))
    data_frequency = _parse_enum(DataFrequency, raw.get("data_frequency", "1d"))
    rebalance_frequency = _parse_enum(
        RebalanceFrequency,
        raw.get("rebalance_frequency", data_frequency.value),
    )

    download_config = ProfileDownloadConfig(
        enabled=bool(download_raw.get("enabled", False)),
        provider=str(download_raw.get("provider", data_raw.get("provider", "akshare"))),
        symbols=tuple(str(symbol) for symbol in (download_raw.get("symbols", []) or [])),
        start_date=(
            str(download_raw["start_date"])
            if download_raw.get("start_date") is not None
            else None
        ),
        end_date=(
            str(download_raw["end_date"])
            if download_raw.get("end_date") is not None
            else None
        ),
        options=dict(download_raw.get("options", {}) or {}),
    )
    data_config = ProfileDataConfig(
        provider=str(data_raw.get("provider", "akshare")),
        dataset_id=str(data_raw.get("dataset_id", "core")),
        path=str(
            data_raw.get(
                "path",
                f"{market.value.lower()}/"
                f"{asset_type.value.lower()}/"
                f"{data_frequency.value.lower()}/core.parquet",
            )
        ),
        price_field=str(
            data_raw.get(
                "price_field",
                "adjusted_close" if data_frequency in {DataFrequency.D1, DataFrequency.W1} else "close",
            )
        ),
        adjusted=bool(data_raw.get("adjusted", True)),
        live_trading_eligible=_parse_bool(
            data_raw.get("live_trading_eligible", False),
            field_name="data.live_trading_eligible",
        ),
        download=download_config,
    )
    futures_config = None
    if futures_raw is not None:
        if not isinstance(futures_raw, dict):
            raise ValueError("配置字段 futures 必须是对象")
        futures_config = ProfileFuturesConfig(
            contract_spec_path=str(futures_raw.get("contract_spec_path", "")).strip(),
            ctp_contract_mapping_path=str(
                futures_raw.get("ctp_contract_mapping_path", "")
            ).strip(),
            symbols_are_continuous=_parse_bool(
                futures_raw.get("symbols_are_continuous", True),
                field_name="futures.symbols_are_continuous",
            ),
            execution_allowed=_parse_bool(
                futures_raw.get("execution_allowed", False),
                field_name="futures.execution_allowed",
            ),
        )
    if asset_type == AssetType.FUTURES:
        if (
            futures_config is None
            or not futures_config.contract_spec_path
            or not futures_config.ctp_contract_mapping_path
        ):
            raise ValueError(
                "期货画像必须配置 futures.contract_spec_path 和 "
                "futures.ctp_contract_mapping_path"
            )
        if futures_config.symbols_are_continuous and futures_config.execution_allowed:
            raise ValueError("连续合约只能用于研究，不能设置 futures.execution_allowed=true")
        if futures_config.execution_allowed and not futures_config.ctp_contract_mapping_path:
            raise ValueError("可执行期货画像必须配置 futures.ctp_contract_mapping_path")
    elif futures_config is not None:
        raise ValueError("只有 FUTURES 画像可以配置 futures")
    lifecycle_config = ProfileLifecycleConfig(
        role=str(lifecycle_raw.get("role", "experimental")).strip().lower(),
        line_id=str(
            lifecycle_raw.get(
                "line_id",
                raw.get("profile_id", resolved_profile_id),
            )
        ),
    )
    execution_config = ProfileExecutionConfig(
        long_only=bool(execution_raw.get("long_only", True)),
        rebalance_min_trade_value=(
            float(execution_raw["rebalance_min_trade_value"])
            if execution_raw.get("rebalance_min_trade_value") is not None
            else None
        ),
        rebalance_weight_tolerance=float(execution_raw.get("rebalance_weight_tolerance", 0.0) or 0.0),
        order_qty_step=(
            float(execution_raw["order_qty_step"])
            if execution_raw.get("order_qty_step") is not None
            else None
        ),
        buy_qty_step=(
            float(execution_raw["buy_qty_step"])
            if execution_raw.get("buy_qty_step") is not None
            else None
        ),
        sell_qty_step=(
            float(execution_raw["sell_qty_step"])
            if execution_raw.get("sell_qty_step") is not None
            else None
        ),
    )
    backtest_engine = str(backtest_raw.get("engine", "weight_return")).strip().lower()
    if backtest_engine not in {"weight_return"}:
        raise ValueError(
            "配置字段 backtest.engine 当前仅支持期货连续合约研究引擎 weight_return"
        )
    backtest_config = ProfileBacktestConfig(
        engine=backtest_engine,
        initial_cash=float(backtest_raw.get("initial_cash", 100_000.0)),
        commission_bps=float(backtest_raw.get("commission_bps", 0.0)),
        min_commission=float(backtest_raw.get("min_commission", 0.0)),
        slippage_bps=float(backtest_raw.get("slippage_bps", 0.0)),
        lot_size=_parse_positive_int(
            backtest_raw.get("lot_size", 1),
            field_name="backtest.lot_size",
            minimum=1,
        ),
        execution_delay_sessions=_parse_positive_int(
            backtest_raw.get("execution_delay_sessions", 1),
            field_name="backtest.execution_delay_sessions",
            minimum=1,
        ),
        sellable_after_sessions=_parse_positive_int(
            backtest_raw.get("sellable_after_sessions", 0),
            field_name="backtest.sellable_after_sessions",
            minimum=0,
        ),
    )
    version_config = ProfileVersionConfig(
        profile=str(versions_raw.get("profile", "v1")),
        benchmark=str(versions_raw.get("benchmark", "v1")),
        strategy_params=str(versions_raw.get("strategy_params", "v1")),
        execution_policy=str(versions_raw.get("execution_policy", "v1")),
        risk_policy=str(versions_raw.get("risk_policy", "v1")),
    )

    strategy_configs = tuple(
        ProfileStrategyConfig(
            strategy_id=str(item["strategy_id"]),
            strategy_family=(
                _parse_enum(StrategyFamily, item["strategy_family"])
                if item.get("strategy_family") is not None
                else None
            ),
            capital_weight=float(item.get("capital_weight", 1.0)),
            enabled=bool(item.get("enabled", True)),
            params=dict(item.get("params", {}) or {}),
        )
        for item in strategies_raw
    )

    default_strategy_family = (
        strategy_configs[0].strategy_family.value
        if strategy_configs and strategy_configs[0].strategy_family is not None
        else StrategyFamily.TREND_FOLLOWING.value
    )

    metadata = {
        key: value
        for key, value in raw.items()
        if key
        not in {
            "profile_id",
            "name",
            "market",
            "asset_type",
            "data_frequency",
            "rebalance_frequency",
            "strategy_family",
            "currency",
            "timezone",
            "calendar",
            "universe_id",
            "benchmark_symbol",
            "data",
            "futures",
            "strategies",
            "lifecycle",
            "execution",
            "backtest",
            "versions",
            "risk",
            "schedule",
        }
    }

    return TradingProfile(
        profile_id=str(raw.get("profile_id", resolved_profile_id)),
        name=str(raw.get("name", resolved_profile_id)),
        market=market,
        asset_type=asset_type,
        data_frequency=data_frequency,
        rebalance_frequency=rebalance_frequency,
        strategy_family=_parse_enum(
            StrategyFamily,
            raw.get("strategy_family", default_strategy_family),
        ),
        currency=str(raw.get("currency", get_settings().trading_currency)).upper(),
        timezone=str(raw.get("timezone", get_settings().timezone)),
        calendar=str(raw.get("calendar", get_settings().exchange_calendar)),
        universe_id=str(raw.get("universe_id", resolved_profile_id)),
        benchmark_symbol=str(raw.get("benchmark_symbol", get_settings().report_benchmark_symbol)),
        data=data_config,
        futures=futures_config,
        strategies=strategy_configs,
        lifecycle=lifecycle_config,
        execution=execution_config,
        backtest=backtest_config,
        versions=version_config,
        risk=dict(raw.get("risk", {}) or {}),
        schedule=dict(raw.get("schedule", {}) or {}),
        metadata=metadata,
    )


def _parse_positive_int(value: object, *, field_name: str, minimum: int) -> int:
    """严格读取画像中的整数，避免 YAML 小数或布尔值静默进入数量规则。"""

    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"配置字段 {field_name} 必须是大于等于 {minimum} 的整数")
    return value
