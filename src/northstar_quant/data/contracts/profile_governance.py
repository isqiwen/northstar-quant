"""数据治理与交易画像之间的显式边界校验。

交易画像的结构解析属于 Foundation Configuration；数据源、品种池和研究准入政策之间的
业务一致性属于 Data 领域。应用组合层及数据制品读写入口必须调用本模块，避免
Foundation 反向依赖业务领域，同时保持缺少治理证据时的失败关闭行为。
"""

from __future__ import annotations

from northstar_quant.data.contracts.instrument_universes import load_instrument_universe
from northstar_quant.foundation.common.enums import AssetType
from northstar_quant.foundation.config.data_sources import get_data_source
from northstar_quant.foundation.config.research_admission import load_research_admission_policy
from northstar_quant.foundation.config.trading_profile import TradingProfile


def validate_profile_data_governance(profile: TradingProfile) -> None:
    """校验画像绑定的数据治理外键。

    没有 ``source_id`` 的临时测试画像仍可用于纯结构测试；仓库正式画像和所有数据
    读取、下载、导入入口则必须通过完整校验。该函数不放宽任何 live-trading 资格。
    """

    if not profile.data.source_id:
        return

    source = get_data_source(profile.data.source_id)
    if source.adapter_id != profile.data.provider:
        raise ValueError(
            f"画像 {profile.profile_id} 的 data.provider={profile.data.provider} 与 "
            f"数据源 {source.source_id} 的 adapter_id={source.adapter_id} 不一致"
        )
    if profile.data.download.enabled and source.adapter_id != profile.data.download.provider:
        raise ValueError(
            f"画像 {profile.profile_id} 的 data.download.provider 与数据源 adapter_id 不一致"
        )
    if not source.supports(
        market=profile.market.value,
        asset_type=profile.asset_type.value,
        frequency=profile.data_frequency.value,
    ):
        raise ValueError(f"数据源 {source.source_id} 不支持画像 {profile.profile_id} 的数据维度")
    if profile.data.live_trading_eligible and not source.license.allows_live_trading:
        raise ValueError(
            f"画像 {profile.profile_id} 声明 live_trading_eligible=true，"
            f"但数据源 {source.source_id} 未授权 live_trading"
        )

    universe = load_instrument_universe(profile.universe_id)
    if universe.market != profile.market.value or universe.asset_type != profile.asset_type.value:
        raise ValueError(f"画像 {profile.profile_id} 与品种池 {universe.universe_id} 的维度不一致")
    if profile.benchmark_symbol.upper() not in universe.continuous_symbols:
        raise ValueError(
            f"画像 {profile.profile_id} 的 benchmark_symbol 不属于品种池 {universe.universe_id}"
        )
    if profile.data.download.enabled:
        configured_symbols = {symbol.upper() for symbol in profile.data.download.symbols}
        if profile.futures is not None and profile.futures.symbols_are_continuous:
            expected_symbols = set(universe.continuous_symbols)
        elif profile.asset_type == AssetType.FUTURES:
            expected_symbols = set(universe.products)
        else:
            expected_symbols = set()
        if expected_symbols and configured_symbols != expected_symbols:
            raise ValueError(
                f"画像 {profile.profile_id} 的 data.download.symbols 必须与品种池 "
                f"{universe.universe_id} 完全一致"
            )

    if not profile.research_admission.enabled:
        return
    policy_id = profile.research_admission.policy_id
    if policy_id is None:
        raise ValueError("research_admission 已启用但缺少 policy_id")
    policy = load_research_admission_policy(policy_id)
    if (
        policy.scope.market != profile.market.value
        or policy.scope.asset_type != profile.asset_type.value
    ):
        raise ValueError(f"研究准入政策 {policy.policy_id} 与画像 {profile.profile_id} 的维度不一致")
    if profile.backtest.engine not in policy.scope.allowed_backtest_engines:
        raise ValueError(
            f"研究准入政策 {policy.policy_id} 不适用于回测器 {profile.backtest.engine}"
        )
