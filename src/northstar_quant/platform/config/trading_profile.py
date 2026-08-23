"""交易画像加载与解析。"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Any, TypeVar, cast

from northstar_quant.platform.common.enums import (
    AssetType,
    DataFrequency,
    Market,
    RebalanceFrequency,
    StrategyFamily,
    StringEnum,
)
from northstar_quant.platform.common.types import TradingDimensions
from northstar_quant.platform.config.settings import get_settings
from northstar_quant.platform.config.yaml_loader import load_yaml


EnumValue = TypeVar("EnumValue", bound=StringEnum)

_PROFILE_TOP_LEVEL_FIELDS = frozenset(
    {
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
        "portfolio_risk_approval",
        "risk",
        "schedule",
        "research_admission",
        "metadata",
    }
)


_PORTFOLIO_RISK_LIMIT_FIELDS = (
    "per_contract",
    "per_commodity",
    "per_sector",
    "per_exchange",
    "per_strategy",
    "per_account",
    "gross_leverage",
    "net_leverage",
    "margin_utilization",
)
_PORTFOLIO_RISK_SCENARIO_KINDS = frozenset(
    {
        "gap",
        "limit_move",
        "volatility_shock",
        "liquidity_collapse",
        "correlated_commodity_shock",
        "margin_increase",
        "fx_shock",
    }
)
_PORTFOLIO_RISK_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


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


def _parse_string_mapping(
    value: object,
    *,
    field_name: str,
    uppercase_keys: bool = False,
) -> dict[str, str]:
    """严格解析稳定字符串映射，避免日历交易所键或制品 hash 静默变形。"""

    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"配置字段 {field_name} 必须是对象")
    parsed: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise ValueError(f"配置字段 {field_name} 的键必须是非空字符串")
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise ValueError(f"配置字段 {field_name}.{raw_key} 必须是非空字符串")
        key = raw_key.strip().upper() if uppercase_keys else raw_key.strip()
        if key in parsed:
            raise ValueError(f"配置字段 {field_name} 包含重复键：{key}")
        parsed[key] = raw_value.strip()
    return parsed


def _required_config_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _normalized_product_id(value: object, *, field_name: str) -> str:
    return _required_config_text(value, field_name=field_name).lower()


def _portfolio_risk_identifier(value: object, *, field_name: str) -> str:
    normalized = _required_config_text(value, field_name=field_name)
    if _PORTFOLIO_RISK_IDENTIFIER_RE.fullmatch(normalized) is None:
        raise ValueError(
            f"{field_name} must match [A-Za-z0-9][A-Za-z0-9._:-]*"
        )
    return normalized


def _non_negative_finite_number(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a non-negative finite number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError(f"{field_name} must be a non-negative finite number")
    return normalized


def _positive_finite_number(value: object, *, field_name: str) -> float:
    normalized = _non_negative_finite_number(value, field_name=field_name)
    if normalized <= 0:
        raise ValueError(f"{field_name} must be a positive finite number")
    return normalized


def _positive_fraction(value: object, *, field_name: str) -> float:
    normalized = _non_negative_finite_number(value, field_name=field_name)
    if normalized <= 0 or normalized > 1:
        raise ValueError(f"{field_name} must be a finite number in (0, 1]")
    return normalized


def _canonical_config_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _validate_profile_top_level_fields(raw: dict[str, Any]) -> None:
    """拒绝拼错的顶层画像字段，避免风控或回测配置静默失效。"""

    unknown = sorted(set(raw).difference(_PROFILE_TOP_LEVEL_FIELDS))
    if unknown:
        raise ValueError(
            "交易画像包含未知顶层字段："
            + ", ".join(unknown)
            + "；仅允许显式 metadata 承载非运行时信息"
        )
    metadata = raw.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("配置字段 metadata 必须是对象")


@dataclass(frozen=True, slots=True)
class ProfileDownloadConfig:
    """交易画像中的数据下载配置；只描述如何取得数据，不代表数据已经可信。"""

    enabled: bool = False  # 是否允许 ``data download`` 为该画像发起自动下载。
    provider: str = "akshare"  # 下载提供器 ID，如连续合约 akshare 或实际日线 akshare_actual_daily。
    symbols: tuple[str, ...] = ()  # 下载标的池，使用数据源可识别的符号格式。
    start_date: str | None = None  # 下载起始日期，YYYY-MM-DD；为空时由提供器决定。
    end_date: str | None = None  # 下载结束日期，YYYY-MM-DD；为空通常下载至最新可得日期。
    options: dict[str, Any] = field(default_factory=dict)  # 仅传给对应提供器的扩展参数。


@dataclass(frozen=True, slots=True)
class ProfileDataConfig:
    """交易画像中的标准数据集、价格口径和真实交易资格。"""

    provider: str = "akshare"  # 标准数据集的来源标识，必须与数据实际血缘一致。
    source_id: str = ""  # 数据法律/运营来源 ID；与技术 adapter provider 分离，不能为空时会严格校验。
    dataset_id: str = "core"  # 数据集逻辑版本/名称，用于 manifest 与画像匹配。
    path: str = ""  # 相对 storage/market 的标准化数据文件路径。
    signal_frequency: DataFrequency | None = None  # 策略实际读取的频率；为空时等于原始行情频率。
    price_field: str = "close"  # 策略和研究使用的价格列，如 close 或 adjusted_close。
    adjusted: bool = True  # 数据集是否按复权语义处理；不替代 price_field 的明确选择。
    live_trading_eligible: bool = False  # 真实交易数据资格开关，默认 false 且不能单独放行下单。
    download: ProfileDownloadConfig = field(default_factory=ProfileDownloadConfig)  # 数据取得规则。


@dataclass(frozen=True, slots=True)
class ProfileFuturesConfig:
    """期货画像的合约主数据与研究/执行边界。"""

    contract_spec_path: str = ""  # 相对项目根目录的期货合约规格文件路径。
    ctp_contract_mapping_path: str = ""  # 连续合约到 CTP 具体合约的显式映射文件。
    calendar_artifact_snapshot_hashes: dict[str, str] = field(default_factory=dict)  # 按交易所绑定的不可变日历制品快照；空值不能放行订单。
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

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            raise ValueError("strategies.strategy_id 必须是非空字符串")
        object.__setattr__(self, "strategy_id", self.strategy_id.strip())

        if isinstance(self.capital_weight, bool):
            raise ValueError("strategies.capital_weight 必须是有限且非负的数值")
        try:
            capital_weight = float(self.capital_weight)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "strategies.capital_weight 必须是有限且非负的数值"
            ) from exc
        if not math.isfinite(capital_weight) or capital_weight < 0:
            raise ValueError("strategies.capital_weight 必须是有限且非负的数值")
        object.__setattr__(self, "capital_weight", capital_weight)
        if not isinstance(self.enabled, bool):
            raise ValueError("strategies.enabled 必须是明确的布尔值")


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

    def __post_init__(self) -> None:
        if (
            self.rebalance_min_trade_value is not None
            and self.rebalance_min_trade_value < 0
        ):
            raise ValueError("execution.rebalance_min_trade_value 不能为负数")
        if not 0.0 <= self.rebalance_weight_tolerance <= 1.0:
            raise ValueError("execution.rebalance_weight_tolerance 必须在 [0, 1] 内")
        for field_name in ("order_qty_step", "buy_qty_step", "sell_qty_step"):
            value = getattr(self, field_name)
            if value is not None and value <= 0:
                raise ValueError(f"execution.{field_name} 必须大于 0")


@dataclass(frozen=True, slots=True)
class ProfileBacktestConfig:
    """交易画像中的回测引擎与显式撮合假设；示例费率不能直接视为券商费率。"""

    engine: str = "weight_return"  # 连续合约研究回测器。
    initial_cash: float = 100_000.0  # 回测初始现金，不代表真实账户余额。
    commission_bps: float = 0.0  # 单边费率，单位为基点；1 bp = 0.01%。
    min_commission: float = 0.0  # 单笔最低佣金，计价货币与画像 currency 一致。
    slippage_bps: float = 0.0  # 成交滑点假设，单位为基点；用于保守模拟。
    slippage_ticks: float = 0.0  # 实际期货合约逐日撮合的单边滑点 tick 数。
    max_volume_participation: float = 1.0  # 单日单合约最多参与成交量的比例。
    lot_size: int = 1  # 撮合数量必须满足的最小整手单位。
    execution_delay_sessions: int = 1  # 信号产生后延迟多少个交易时段成交，至少为 1 防未来函数。
    sellable_after_sessions: int = 0  # 保留的通用撮合字段；期货 T+0 研究画像应为 0。
    order_ttl_bars: int = 1  # 分钟回放委托最多经过多少根可交易 bar，超时后自动撤单。
    queue_ahead_ratio: float = 0.0  # 被动限价单假设排在当根成交量之前的比例。

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError("backtest.initial_cash 必须大于 0")
        for field_name in ("commission_bps", "min_commission", "slippage_bps", "slippage_ticks"):
            if getattr(self, field_name) < 0:
                raise ValueError(f"backtest.{field_name} 不能为负数")
        if not 0 < self.max_volume_participation <= 1:
            raise ValueError("backtest.max_volume_participation 必须位于 (0, 1]")
        if self.lot_size < 1:
            raise ValueError("backtest.lot_size 至少为 1")
        if self.execution_delay_sessions < 1:
            raise ValueError("backtest.execution_delay_sessions 至少为 1")
        if self.sellable_after_sessions < 0:
            raise ValueError("backtest.sellable_after_sessions 不能为负数")
        if self.order_ttl_bars < 1:
            raise ValueError("backtest.order_ttl_bars 至少为 1")
        if not 0 <= self.queue_ahead_ratio <= 1:
            raise ValueError("backtest.queue_ahead_ratio 必须位于 [0, 1]")


@dataclass(frozen=True, slots=True)
class ProfileVersionConfig:
    """可追溯性锚点；字段是版本标签，不会自动加载或比较外部配置。"""

    profile: str = "v1"  # 画像整体版本。
    benchmark: str = "v1"  # 基准定义及总回报口径版本。
    strategy_params: str = "v1"  # 策略参数集合版本。
    execution_policy: str = "v1"  # 成交、费用、交易单位等执行假设版本。
    risk_policy: str = "v1"  # 风控阈值和规则版本。


@dataclass(frozen=True, slots=True)
class PortfolioRiskLimitConfig:
    """The exact nine P3 portfolio-risk limits selected by one profile policy."""

    per_contract: float
    per_commodity: float
    per_sector: float
    per_exchange: float
    per_strategy: float
    per_account: float
    gross_leverage: float
    net_leverage: float
    margin_utilization: float

    def __post_init__(self) -> None:
        for field_name in _PORTFOLIO_RISK_LIMIT_FIELDS:
            object.__setattr__(
                self,
                field_name,
                _non_negative_finite_number(
                    getattr(self, field_name),
                    field_name=f"portfolio_risk_approval.limits.{field_name}",
                ),
            )

    def as_mapping(self) -> dict[str, object]:
        return {
            field_name: getattr(self, field_name)
            for field_name in _PORTFOLIO_RISK_LIMIT_FIELDS
        }


@dataclass(frozen=True, slots=True)
class PortfolioRiskScenarioConfig:
    """One profile-owned P3 stress scenario and its approval thresholds."""

    kind: str
    scenario_id: str
    shock_fraction: float
    max_loss_fraction: float
    max_margin_utilization: float

    def __post_init__(self) -> None:
        kind = _required_config_text(
            self.kind,
            field_name="portfolio_risk_approval.scenarios.kind",
        ).lower()
        if kind not in _PORTFOLIO_RISK_SCENARIO_KINDS:
            raise ValueError(
                "portfolio_risk_approval.scenarios.kind must be one of: "
                + ", ".join(sorted(_PORTFOLIO_RISK_SCENARIO_KINDS))
            )
        object.__setattr__(self, "kind", kind)
        object.__setattr__(
            self,
            "scenario_id",
            _required_config_text(
                self.scenario_id,
                field_name="portfolio_risk_approval.scenarios.scenario_id",
            ),
        )
        object.__setattr__(
            self,
            "shock_fraction",
            _positive_finite_number(
                self.shock_fraction,
                field_name="portfolio_risk_approval.scenarios.shock_fraction",
            ),
        )
        for field_name in ("max_loss_fraction", "max_margin_utilization"):
            object.__setattr__(
                self,
                field_name,
                _non_negative_finite_number(
                    getattr(self, field_name),
                    field_name=f"portfolio_risk_approval.scenarios.{field_name}",
                ),
            )

    def as_mapping(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "scenario_id": self.scenario_id,
            "shock_fraction": self.shock_fraction,
            "max_loss_fraction": self.max_loss_fraction,
            "max_margin_utilization": self.max_margin_utilization,
        }


@dataclass(frozen=True, slots=True)
class PortfolioRiskTaxonomyEntry:
    """Profile-owned classification for one executable product family."""

    product_id: str
    commodity_id: str
    sector_id: str
    correlation_cluster_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "product_id",
            _normalized_product_id(
                self.product_id,
                field_name="portfolio_risk_approval.taxonomy product_id",
            ),
        )
        for field_name in (
            "commodity_id",
            "sector_id",
            "correlation_cluster_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_config_text(
                    getattr(self, field_name),
                    field_name=f"portfolio_risk_approval.taxonomy.{field_name}",
                ),
            )

    def as_mapping(self) -> dict[str, object]:
        return {
            "product_id": self.product_id,
            "commodity_id": self.commodity_id,
            "sector_id": self.sector_id,
            "correlation_cluster_id": self.correlation_cluster_id,
        }


@dataclass(frozen=True, slots=True)
class CtpSimPortfolioRiskExecutionRule:
    """Simulator-only execution limits owned by the selected risk policy."""

    product_id: str
    margin_rate: float
    max_position_lots: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "product_id",
            _normalized_product_id(
                self.product_id,
                field_name="portfolio_risk_approval.ctp_sim_execution_rules product_id",
            ),
        )
        object.__setattr__(
            self,
            "margin_rate",
            _positive_fraction(
                self.margin_rate,
                field_name="portfolio_risk_approval.ctp_sim_execution_rules.margin_rate",
            ),
        )
        if (
            isinstance(self.max_position_lots, bool)
            or not isinstance(self.max_position_lots, int)
            or self.max_position_lots < 1
        ):
            raise ValueError(
                "portfolio_risk_approval.ctp_sim_execution_rules.max_position_lots "
                "must be a positive integer"
            )

    def as_mapping(self) -> dict[str, object]:
        return {
            "product_id": self.product_id,
            "margin_rate": self.margin_rate,
            "max_position_lots": self.max_position_lots,
        }


@dataclass(frozen=True, slots=True)
class ProfilePortfolioRiskApprovalConfig:
    """Complete typed policy authority for one P3 approval boundary.

    This record intentionally has no runtime defaults.  A profile either names
    the complete policy it wants to use, or a CTP-sim caller must fail closed.
    """

    policy_id: str
    policy_version: str
    max_input_age_seconds: int
    manual_approval_verifier_id: str
    authorized_approver_ids: tuple[str, ...]
    limits: PortfolioRiskLimitConfig
    scenarios: tuple[PortfolioRiskScenarioConfig, ...]
    taxonomy: tuple[PortfolioRiskTaxonomyEntry, ...]
    ctp_sim_execution_rules: tuple[CtpSimPortfolioRiskExecutionRule, ...]
    config_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "policy_id",
            _portfolio_risk_identifier(
                self.policy_id,
                field_name="portfolio_risk_approval.policy_id",
            ),
        )
        object.__setattr__(
            self,
            "policy_version",
            _portfolio_risk_identifier(
                self.policy_version,
                field_name="portfolio_risk_approval.policy_version",
            ),
        )
        object.__setattr__(
            self,
            "max_input_age_seconds",
            _parse_positive_int(
                self.max_input_age_seconds,
                field_name="portfolio_risk_approval.max_input_age_seconds",
                minimum=1,
            ),
        )
        object.__setattr__(
            self,
            "manual_approval_verifier_id",
            _portfolio_risk_identifier(
                self.manual_approval_verifier_id,
                field_name="portfolio_risk_approval.manual_approval_verifier_id",
            ),
        )
        if (
            not isinstance(self.authorized_approver_ids, tuple)
            or not self.authorized_approver_ids
        ):
            raise ValueError(
                "portfolio_risk_approval.authorized_approver_ids must be a non-empty tuple"
            )
        authorized_approver_ids = tuple(
            sorted(
                _portfolio_risk_identifier(
                    item,
                    field_name="portfolio_risk_approval.authorized_approver_ids",
                )
                for item in self.authorized_approver_ids
            )
        )
        if len(set(authorized_approver_ids)) != len(authorized_approver_ids):
            raise ValueError(
                "portfolio_risk_approval.authorized_approver_ids cannot contain duplicates"
            )
        if type(self.limits) is not PortfolioRiskLimitConfig:
            raise ValueError(
                "portfolio_risk_approval.limits must be a PortfolioRiskLimitConfig"
            )
        if (
            not isinstance(self.scenarios, tuple)
            or len(self.scenarios) != len(_PORTFOLIO_RISK_SCENARIO_KINDS)
            or not all(type(item) is PortfolioRiskScenarioConfig for item in self.scenarios)
        ):
            raise ValueError(
                "portfolio_risk_approval.scenarios must contain exactly one typed entry "
                "for every P3 scenario kind"
            )
        scenarios = tuple(sorted(self.scenarios, key=lambda item: item.kind))
        if {item.kind for item in scenarios} != _PORTFOLIO_RISK_SCENARIO_KINDS:
            raise ValueError(
                "portfolio_risk_approval.scenarios must cover every P3 scenario kind exactly once"
            )
        if len({item.scenario_id for item in scenarios}) != len(scenarios):
            raise ValueError("portfolio_risk_approval.scenarios cannot duplicate scenario_id")

        if (
            not isinstance(self.taxonomy, tuple)
            or not self.taxonomy
            or not all(type(item) is PortfolioRiskTaxonomyEntry for item in self.taxonomy)
        ):
            raise ValueError(
                "portfolio_risk_approval.taxonomy must be a non-empty typed product mapping"
            )
        taxonomy = tuple(sorted(self.taxonomy, key=lambda item: item.product_id))
        if len({item.product_id for item in taxonomy}) != len(taxonomy):
            raise ValueError("portfolio_risk_approval.taxonomy cannot duplicate product_id")

        if (
            not isinstance(self.ctp_sim_execution_rules, tuple)
            or not self.ctp_sim_execution_rules
            or not all(
                type(item) is CtpSimPortfolioRiskExecutionRule
                for item in self.ctp_sim_execution_rules
            )
        ):
            raise ValueError(
                "portfolio_risk_approval.ctp_sim_execution_rules must be a non-empty "
                "typed product mapping"
            )
        rules = tuple(sorted(self.ctp_sim_execution_rules, key=lambda item: item.product_id))
        if len({item.product_id for item in rules}) != len(rules):
            raise ValueError(
                "portfolio_risk_approval.ctp_sim_execution_rules cannot duplicate product_id"
            )
        if {item.product_id for item in taxonomy} != {item.product_id for item in rules}:
            raise ValueError(
                "portfolio_risk_approval.taxonomy and ctp_sim_execution_rules must cover "
                "the exact same product_id set"
            )

        object.__setattr__(self, "scenarios", scenarios)
        object.__setattr__(self, "taxonomy", taxonomy)
        object.__setattr__(self, "ctp_sim_execution_rules", rules)
        object.__setattr__(self, "authorized_approver_ids", authorized_approver_ids)
        object.__setattr__(
            self,
            "config_hash",
            _canonical_config_hash(self.as_mapping(False)),
        )

    def as_mapping(self, include_hash: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "format": "northstar.profile-portfolio-risk-approval.v1",
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "max_input_age_seconds": self.max_input_age_seconds,
            "manual_approval_verifier_id": self.manual_approval_verifier_id,
            "authorized_approver_ids": list(self.authorized_approver_ids),
            "limits": self.limits.as_mapping(),
            "scenarios": [item.as_mapping() for item in self.scenarios],
            "taxonomy": [item.as_mapping() for item in self.taxonomy],
            "ctp_sim_execution_rules": [
                item.as_mapping() for item in self.ctp_sim_execution_rules
            ],
        }
        if include_hash:
            result["config_hash"] = self.config_hash
        return result

    def taxonomy_for(self, product_id: str) -> PortfolioRiskTaxonomyEntry:
        normalized = _normalized_product_id(
            product_id,
            field_name="portfolio_risk_approval taxonomy lookup product_id",
        )
        for item in self.taxonomy:
            if item.product_id == normalized:
                return item
        raise KeyError(
            "portfolio_risk_approval.taxonomy does not configure product_id: "
            + normalized
        )

    def ctp_sim_execution_rule_for(
        self,
        product_id: str,
    ) -> CtpSimPortfolioRiskExecutionRule:
        normalized = _normalized_product_id(
            product_id,
            field_name="portfolio_risk_approval ctp_sim rule lookup product_id",
        )
        for item in self.ctp_sim_execution_rules:
            if item.product_id == normalized:
                return item
        raise KeyError(
            "portfolio_risk_approval.ctp_sim_execution_rules does not configure "
            "product_id: "
            + normalized
        )


@dataclass(frozen=True, slots=True)
class ProfileResearchAdmissionConfig:
    """画像绑定的候选策略研究准入政策。

    这不是运行时风控，也不会改变 ``execution_allowed``。启用后仅在离线回测报告中形成
    可审计结论；候选提升仍需独立人工审批。
    """

    enabled: bool = False
    policy_id: str | None = None

    def __post_init__(self) -> None:
        if self.policy_id is not None:
            if not isinstance(self.policy_id, str) or not self.policy_id.strip():
                raise ValueError("research_admission.policy_id 必须是非空字符串或 null")
            object.__setattr__(self, "policy_id", self.policy_id.strip())
        if self.enabled and self.policy_id is None:
            raise ValueError("research_admission.enabled=true 时必须配置 policy_id")


@dataclass(frozen=True, slots=True)
class TradingProfile:
    """统一描述一次完整交易流程；字段注释是 YAML 的运行时语义参考。"""

    profile_id: str  # 全局唯一 ID；必须等于文件名且带 _offline/_simulated/_live 后缀。
    name: str  # 面向人类的简短中文名称，不参与程序路由。
    market: Market  # 市场枚举：CN、US、HK；影响日历、规则和适配器选择。
    asset_type: AssetType  # 本项目固定为 FUTURES。
    data_frequency: DataFrequency  # 原始行情频率，如 1d、1w、1m。
    rebalance_frequency: RebalanceFrequency  # 允许调整目标持仓的频率，不必等于数据频率。
    strategy_family: StrategyFamily  # 画像默认策略分类；具体策略可在 strategies 内覆盖。
    currency: str  # 计价货币，当前仅支持 CNY；费用和金额阈值均使用该货币。
    timezone: str  # IANA 时区，如 Asia/Shanghai；用于调度与报告日期边界。
    calendar: str  # 交易日历逻辑标识；真实期货执行还必须配置经验证的不可变快照。
    universe_id: str  # 标的池/研究宇宙的稳定版本名称，不直接携带标的列表。
    benchmark_symbol: str  # 基准标的符号，用于报告和相对表现比较。
    data: ProfileDataConfig  # 行情来源、落盘位置、价格口径和下载规则。
    futures: ProfileFuturesConfig | None = None  # 仅 FUTURES 画像必填，声明连续合约与执行边界。
    strategies: tuple[ProfileStrategyConfig, ...] = ()  # 画像启用的策略清单与组合权重。
    lifecycle: ProfileLifecycleConfig = field(default_factory=ProfileLifecycleConfig)  # 连接边界与角色。
    execution: ProfileExecutionConfig = field(default_factory=ProfileExecutionConfig)  # 再平衡/手数执行约束。
    backtest: ProfileBacktestConfig = field(default_factory=ProfileBacktestConfig)  # 回测撮合假设。
    versions: ProfileVersionConfig = field(default_factory=ProfileVersionConfig)  # 可追溯性版本锚点。
    portfolio_risk_approval: ProfilePortfolioRiskApprovalConfig | None = None
    research_admission: ProfileResearchAdmissionConfig = field(
        default_factory=ProfileResearchAdmissionConfig
    )  # 候选研究准入政策绑定。
    risk: dict[str, Any] = field(default_factory=dict)  # RiskLimits 支持字段的画像级覆盖，未知字段会拒绝。
    schedule: dict[str, Any] = field(default_factory=dict)  # cron 配置，如 daily_signal_cron、execution_cron。
    metadata: dict[str, Any] = field(default_factory=dict)  # 显式非运行时元数据；不应假定影响交易行为。

    def __post_init__(self) -> None:
        if (
            self.portfolio_risk_approval is not None
            and type(self.portfolio_risk_approval) is not ProfilePortfolioRiskApprovalConfig
        ):
            raise ValueError(
                "portfolio_risk_approval must be a ProfilePortfolioRiskApprovalConfig or None"
            )
        strategy_ids = [strategy.strategy_id for strategy in self.strategies]
        duplicate_ids = sorted(
            strategy_id
            for strategy_id in set(strategy_ids)
            if strategy_ids.count(strategy_id) > 1
        )
        if duplicate_ids:
            raise ValueError(
                "交易画像 strategies.strategy_id 不能重复："
                + ", ".join(duplicate_ids)
            )

        enabled_strategies = [
            strategy for strategy in self.strategies if strategy.enabled
        ]
        if not enabled_strategies:
            raise ValueError("交易画像至少需要一条 enabled=true 的策略")
        enabled_weight_total = sum(
            strategy.capital_weight for strategy in enabled_strategies
        )
        if not math.isfinite(enabled_weight_total) or enabled_weight_total <= 0:
            raise ValueError(
                "交易画像 enabled 策略的 capital_weight 总和必须大于 0"
            )

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

    @property
    def strategy_data_frequency(self) -> DataFrequency:
        """返回策略信号频率；分钟回放可使用日线策略信号。"""

        return self.data.signal_frequency or self.data_frequency

    def strategy_dimensions(self, strategy: ProfileStrategyConfig) -> TradingDimensions:
        return TradingDimensions(
            market=self.market,
            asset_type=self.asset_type,
            data_frequency=self.strategy_data_frequency,
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


def ensure_broker_profile(
    profile: TradingProfile,
    *,
    broker: str,
    context: str,
) -> TradingProfile:
    """校验画像与券商边界，禁止仿真和真实账户互相借用画像。"""

    normalized_broker = str(broker or "").strip().lower()
    if normalized_broker != "ctp_sim":
        return ensure_production_profile(profile, context=context)
    if profile.lifecycle.role != "simulated":
        raise ValueError(
            f"{context} 使用 ctp_sim 时仅允许 simulated 画像；"
            f"当前 {profile.profile_id} 的角色为 {profile.lifecycle.role}。"
        )
    futures = profile.futures
    if (
        profile.asset_type != AssetType.FUTURES
        or futures is None
        or futures.symbols_are_continuous
        or not futures.execution_allowed
    ):
        raise ValueError(
            f"{context} 的 ctp_sim 画像必须使用具体期货合约数据并设置 "
            "futures.execution_allowed=true。"
        )
    return profile


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
    """从 YAML 读取可复用的交易画像快照。

    普通研究、回测和 CLI 调用可以复用该缓存。会影响提交授权的长生命周期路径必须使用
    :func:`load_trading_profile_uncached`，以便操作员撤销或替换画像中的不可变制品 pin 后，
    下一笔订单立即看到最新文件内容。
    """

    return _load_trading_profile(profile_id, config_dir)


def load_trading_profile_uncached(
    profile_id: str | None = None,
    config_dir: str | Path | None = None,
) -> TradingProfile:
    """不使用进程缓存读取交易画像，供逐订单安全门禁重新核验。"""

    return _load_trading_profile(profile_id, config_dir)


def _load_trading_profile(
    profile_id: str | None = None,
    config_dir: str | Path | None = None,
) -> TradingProfile:
    """执行一次完整的画像文件解析和结构校验。"""

    resolved_profile_id = resolve_profile_id(profile_id)
    path = get_profile_config_path(resolved_profile_id, config_dir)
    if not path.exists():
        available_profiles = ", ".join(list_trading_profiles(config_dir)) or "无"
        raise FileNotFoundError(
            f"交易画像配置不存在：{path}。当前可用画像：{available_profiles}"
        )

    raw = load_yaml(path)
    if not isinstance(raw, dict):
        raise ValueError(f"交易画像配置顶层必须是对象：{path}")
    _validate_profile_top_level_fields(raw)
    data_raw = raw.get("data", {}) or {}
    futures_raw = raw.get("futures")
    download_raw = data_raw.get("download", {}) or {}
    strategies_raw = raw.get("strategies", []) or []
    if not isinstance(strategies_raw, list):
        raise ValueError("配置字段 strategies 必须是列表")
    lifecycle_raw = raw.get("lifecycle", {}) or {}
    execution_raw = raw.get("execution", {}) or {}
    backtest_raw = raw.get("backtest", {}) or {}
    versions_raw = raw.get("versions", {}) or {}
    research_admission_raw = raw.get("research_admission", {}) or {}
    if not isinstance(research_admission_raw, dict):
        raise ValueError("配置字段 research_admission 必须是对象")
    unknown_research_admission = sorted(
        set(research_admission_raw).difference({"enabled", "policy_id"})
    )
    if unknown_research_admission:
        raise ValueError(
            "research_admission 包含未知字段：" + ", ".join(unknown_research_admission)
        )

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
        enabled=_parse_bool(
            download_raw.get("enabled", False),
            field_name="data.download.enabled",
        ),
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
        source_id=str(data_raw.get("source_id", "")).strip(),
        dataset_id=str(data_raw.get("dataset_id", "core")),
        path=str(
            data_raw.get(
                "path",
                f"{market.value.lower()}/"
                f"{asset_type.value.lower()}/"
                f"{data_frequency.value.lower()}/core.parquet",
            )
        ),
        signal_frequency=(
            _parse_enum(DataFrequency, data_raw["signal_frequency"])
            if data_raw.get("signal_frequency") is not None
            else None
        ),
        price_field=str(
            data_raw.get(
                "price_field",
                "adjusted_close" if data_frequency in {DataFrequency.D1, DataFrequency.W1} else "close",
            )
        ),
        adjusted=_parse_bool(
            data_raw.get("adjusted", True),
            field_name="data.adjusted",
        ),
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
            calendar_artifact_snapshot_hashes=_parse_string_mapping(
                futures_raw.get("calendar_artifact_snapshot_hashes", {}),
                field_name="futures.calendar_artifact_snapshot_hashes",
                uppercase_keys=True,
            ),
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
        long_only=_parse_bool(
            execution_raw.get("long_only", True),
            field_name="execution.long_only",
        ),
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
    if backtest_engine not in {
        "weight_return",
        "futures_daily",
        "futures_intraday_replay",
    }:
        raise ValueError(
            "配置字段 backtest.engine 仅支持 weight_return、futures_daily "
            "或 futures_intraday_replay"
        )
    backtest_config = ProfileBacktestConfig(
        engine=backtest_engine,
        initial_cash=float(backtest_raw.get("initial_cash", 100_000.0)),
        commission_bps=float(backtest_raw.get("commission_bps", 0.0)),
        min_commission=float(backtest_raw.get("min_commission", 0.0)),
        slippage_bps=float(backtest_raw.get("slippage_bps", 0.0)),
        slippage_ticks=float(backtest_raw.get("slippage_ticks", 0.0)),
        max_volume_participation=float(
            backtest_raw.get("max_volume_participation", 1.0)
        ),
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
        order_ttl_bars=_parse_positive_int(
            backtest_raw.get("order_ttl_bars", 1),
            field_name="backtest.order_ttl_bars",
            minimum=1,
        ),
        queue_ahead_ratio=float(backtest_raw.get("queue_ahead_ratio", 0.0)),
    )
    if backtest_engine in {"futures_daily", "futures_intraday_replay"}:
        if futures_config is None or futures_config.symbols_are_continuous:
            raise ValueError(f"{backtest_engine} 引擎必须使用具体实际合约数据")
        if (
            backtest_engine == "futures_intraday_replay"
            and data_config.download.enabled
        ):
            raise ValueError(
                "futures_intraday_replay 分钟画像只允许导入已核验的本地数据制品"
            )
        if (
            backtest_engine == "futures_daily"
            and data_config.download.enabled
            and data_config.download.provider != "akshare_actual_daily"
        ):
            raise ValueError(
                "futures_daily 自动下载只允许使用 akshare_actual_daily 提供器"
            )
        if any(
            value != 0
            for value in (
                backtest_config.commission_bps,
                backtest_config.min_commission,
                backtest_config.slippage_bps,
            )
        ):
            raise ValueError(
                f"{backtest_engine} 使用动态规则快照计费，"
                "通用 bps/最低佣金字段必须为 0"
            )
    if backtest_engine == "futures_daily" and data_frequency != DataFrequency.D1:
        raise ValueError("futures_daily 引擎的原始行情频率必须为 1d")
    if backtest_engine == "futures_intraday_replay":
        if data_frequency != DataFrequency.M1:
            raise ValueError("futures_intraday_replay 引擎的原始行情频率必须为 1m")
        if data_config.signal_frequency != DataFrequency.D1:
            raise ValueError(
                "futures_intraday_replay 当前必须配置 data.signal_frequency: 1d"
            )
    version_config = ProfileVersionConfig(
        profile=str(versions_raw.get("profile", "v1")),
        benchmark=str(versions_raw.get("benchmark", "v1")),
        strategy_params=str(versions_raw.get("strategy_params", "v1")),
        execution_policy=str(versions_raw.get("execution_policy", "v1")),
        risk_policy=str(versions_raw.get("risk_policy", "v1")),
    )
    portfolio_risk_approval_config = (
        _parse_portfolio_risk_approval_config(
            raw["portfolio_risk_approval"],
            risk_policy_version=version_config.risk_policy,
        )
        if "portfolio_risk_approval" in raw
        else None
    )
    research_admission_config = ProfileResearchAdmissionConfig(
        enabled=_parse_bool(
            research_admission_raw.get("enabled", False),
            field_name="research_admission.enabled",
        ),
        policy_id=(
            str(research_admission_raw["policy_id"])
            if research_admission_raw.get("policy_id") is not None
            else None
        ),
    )

    strategy_configs_list: list[ProfileStrategyConfig] = []
    for index, item in enumerate(strategies_raw):
        if not isinstance(item, dict):
            raise ValueError(f"strategies[{index}] 必须是对象")
        strategy_id_raw = item.get("strategy_id")
        if not isinstance(strategy_id_raw, str) or not strategy_id_raw.strip():
            raise ValueError(f"strategies[{index}].strategy_id 必须是非空字符串")
        params_raw = item.get("params", {}) or {}
        if not isinstance(params_raw, dict):
            raise ValueError(f"strategies[{index}].params 必须是对象")
        strategy_configs_list.append(
            ProfileStrategyConfig(
                strategy_id=strategy_id_raw.strip(),
                strategy_family=(
                    _parse_enum(StrategyFamily, item["strategy_family"])
                    if item.get("strategy_family") is not None
                    else None
                ),
                capital_weight=item.get("capital_weight", 1.0),
                enabled=_parse_bool(
                    item.get("enabled", True),
                    field_name=f"strategies.{strategy_id_raw.strip()}.enabled",
                ),
                params=dict(params_raw),
            )
        )
    strategy_configs = tuple(strategy_configs_list)
    if not strategy_configs:
        raise ValueError("交易画像至少需要一条 enabled=true 的策略")

    default_strategy_family = (
        strategy_configs[0].strategy_family.value
        if strategy_configs and strategy_configs[0].strategy_family is not None
        else StrategyFamily.TREND_FOLLOWING.value
    )

    metadata = dict(raw.get("metadata", {}) or {})

    profile = TradingProfile(
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
        portfolio_risk_approval=portfolio_risk_approval_config,
        research_admission=research_admission_config,
        risk=dict(raw.get("risk", {}) or {}),
        schedule=dict(raw.get("schedule", {}) or {}),
        metadata=metadata,
    )
    return profile


def _parse_positive_int(value: object, *, field_name: str, minimum: int) -> int:
    """严格读取画像中的整数，避免 YAML 小数或布尔值静默进入数量规则。"""

    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"配置字段 {field_name} 必须是大于等于 {minimum} 的整数")
    return value


def _require_exact_config_fields(
    value: dict[str, Any],
    *,
    field_name: str,
    required: frozenset[str],
) -> None:
    missing = sorted(required.difference(value))
    unknown = sorted(set(value).difference(required))
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append("missing=" + ", ".join(missing))
        if unknown:
            details.append("unknown=" + ", ".join(unknown))
        raise ValueError(f"{field_name} has an invalid field set: " + "; ".join(details))


def _parse_portfolio_risk_approval_config(
    value: object,
    *,
    risk_policy_version: str,
) -> ProfilePortfolioRiskApprovalConfig:
    """Parse a complete policy authority without accepting any fallback values."""

    if not isinstance(value, dict):
        raise ValueError("portfolio_risk_approval must be an object")
    _require_exact_config_fields(
        value,
        field_name="portfolio_risk_approval",
        required=frozenset(
            {
                "policy_id",
                "policy_version",
                "max_input_age_seconds",
                "manual_approval_verifier_id",
                "authorized_approver_ids",
                "limits",
                "scenarios",
                "taxonomy",
                "ctp_sim_execution_rules",
            }
        ),
    )

    limits_raw = value["limits"]
    if not isinstance(limits_raw, dict):
        raise ValueError("portfolio_risk_approval.limits must be an object")
    _require_exact_config_fields(
        limits_raw,
        field_name="portfolio_risk_approval.limits",
        required=frozenset(_PORTFOLIO_RISK_LIMIT_FIELDS),
    )
    limits = PortfolioRiskLimitConfig(
        **{
            field_name: limits_raw[field_name]
            for field_name in _PORTFOLIO_RISK_LIMIT_FIELDS
        }
    )

    scenarios_raw = value["scenarios"]
    if not isinstance(scenarios_raw, list):
        raise ValueError("portfolio_risk_approval.scenarios must be a list")
    scenarios: list[PortfolioRiskScenarioConfig] = []
    for index, item in enumerate(scenarios_raw):
        field_name = f"portfolio_risk_approval.scenarios[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{field_name} must be an object")
        _require_exact_config_fields(
            item,
            field_name=field_name,
            required=frozenset(
                {
                    "kind",
                    "scenario_id",
                    "shock_fraction",
                    "max_loss_fraction",
                    "max_margin_utilization",
                }
            ),
        )
        scenarios.append(
            PortfolioRiskScenarioConfig(
                kind=item["kind"],
                scenario_id=item["scenario_id"],
                shock_fraction=item["shock_fraction"],
                max_loss_fraction=item["max_loss_fraction"],
                max_margin_utilization=item["max_margin_utilization"],
            )
        )

    authorized_approver_ids_raw = value["authorized_approver_ids"]
    if (
        not isinstance(authorized_approver_ids_raw, list)
        or not authorized_approver_ids_raw
    ):
        raise ValueError(
            "portfolio_risk_approval.authorized_approver_ids must be a non-empty list"
        )

    taxonomy_raw = value["taxonomy"]
    if not isinstance(taxonomy_raw, dict) or not taxonomy_raw:
        raise ValueError("portfolio_risk_approval.taxonomy must be a non-empty object")
    taxonomy: list[PortfolioRiskTaxonomyEntry] = []
    for raw_product_id, item in taxonomy_raw.items():
        product_id = _normalized_product_id(
            raw_product_id,
            field_name="portfolio_risk_approval.taxonomy product_id",
        )
        field_name = f"portfolio_risk_approval.taxonomy.{product_id}"
        if not isinstance(item, dict):
            raise ValueError(f"{field_name} must be an object")
        _require_exact_config_fields(
            item,
            field_name=field_name,
            required=frozenset(
                {"commodity_id", "sector_id", "correlation_cluster_id"}
            ),
        )
        taxonomy.append(
            PortfolioRiskTaxonomyEntry(
                product_id=product_id,
                commodity_id=item["commodity_id"],
                sector_id=item["sector_id"],
                correlation_cluster_id=item["correlation_cluster_id"],
            )
        )

    rules_raw = value["ctp_sim_execution_rules"]
    if not isinstance(rules_raw, dict) or not rules_raw:
        raise ValueError(
            "portfolio_risk_approval.ctp_sim_execution_rules must be a non-empty object"
        )
    rules: list[CtpSimPortfolioRiskExecutionRule] = []
    for raw_product_id, item in rules_raw.items():
        product_id = _normalized_product_id(
            raw_product_id,
            field_name="portfolio_risk_approval.ctp_sim_execution_rules product_id",
        )
        field_name = f"portfolio_risk_approval.ctp_sim_execution_rules.{product_id}"
        if not isinstance(item, dict):
            raise ValueError(f"{field_name} must be an object")
        _require_exact_config_fields(
            item,
            field_name=field_name,
            required=frozenset({"margin_rate", "max_position_lots"}),
        )
        rules.append(
            CtpSimPortfolioRiskExecutionRule(
                product_id=product_id,
                margin_rate=item["margin_rate"],
                max_position_lots=item["max_position_lots"],
            )
        )

    config = ProfilePortfolioRiskApprovalConfig(
        policy_id=value["policy_id"],
        policy_version=value["policy_version"],
        max_input_age_seconds=value["max_input_age_seconds"],
        manual_approval_verifier_id=value["manual_approval_verifier_id"],
        authorized_approver_ids=tuple(authorized_approver_ids_raw),
        limits=limits,
        scenarios=tuple(scenarios),
        taxonomy=tuple(taxonomy),
        ctp_sim_execution_rules=tuple(rules),
    )
    if config.policy_version != risk_policy_version:
        raise ValueError(
            "portfolio_risk_approval.policy_version must equal versions.risk_policy"
        )
    return config
