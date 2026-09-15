"""Contract-type admission policy shared by collection and whole-contract review.

Supplier metadata identifies the product and delivery method; suffixes and empty
responses never establish either applicability or a completeness exemption.
"""

from dataclasses import asdict, dataclass
from typing import Any

RULE = "contract-requirements/1"
BASIC_REFERENCE = "https://tushare.pro/document/2?doc_id=135"


@dataclass(frozen=True)
class ContractType:
    category: str
    label: str
    delivery: str
    basis: str

    def public(self) -> dict[str, str]:
        return {**asdict(self), "rule": RULE}


def classify(contract: Any) -> ContractType:
    details = contract["details"]
    exchange, product = contract["exchange"], contract["product"]
    delivery = str(details.get("d_mode_desc") or "未知")
    if contract["kind"] != "1":
        return ContractType("SERIES", "主力或连续序列", "不适用", "供应商 fut_type=2")
    if exchange == "DCE" and product in {"L_F", "V_F", "PP_F"}:
        if "月均价" in str(details.get("name", "")) and delivery == "现金交割":
            return ContractType(
                "MONTHLY_AVERAGE",
                "商品月均价期货",
                delivery,
                "DCE 月均价品种代码、合约名称及现金交割元数据一致",
            )
    elif exchange == "CFFEX":
        if product in {"IF", "IH", "IC", "IM"} and delivery == "现金交割":
            return ContractType(
                "EQUITY_INDEX", "股指期货", delivery, "CFFEX 股指品种及现金交割元数据"
            )
        if product in {"TS", "TF", "T", "TL"} and delivery == "实物交割":
            return ContractType(
                "GOVERNMENT_BOND", "国债期货", delivery, "CFFEX 国债品种及实物交割元数据"
            )
    elif exchange == "INE" and product == "EC" and delivery == "现金交割":
        return ContractType(
            "FREIGHT_INDEX", "航运指数期货", delivery, "INE EC 品种及现金交割元数据"
        )
    elif exchange in {"SHFE", "DCE", "CZCE", "INE", "GFEX"} and delivery == "实物交割":
        if isinstance(product, str) and product.isascii() and product.isalpha():
            return ContractType(
                "PHYSICAL_COMMODITY",
                "实物交割商品期货",
                delivery,
                "商品交易所真实合约及实物交割元数据",
            )
    return ContractType(
        "UNKNOWN",
        "合约类型待核实",
        delivery,
        "品种、名称或交割元数据不足/冲突，不能按代码后缀或空响应推断",
    )


@dataclass(frozen=True)
class Requirement:
    applicability: str
    reason: str
    reference: str

    @property
    def collect(self) -> bool:
        return self.applicability == "REQUIRED"


def requirement(profile: ContractType, dataset: str) -> Requirement:
    if dataset in {"continuous", "mapping", "adjusted", "index"}:
        return Requirement(
            "RELATED",
            "独立研究序列；不作为具体月份合约完整性的前提",
            "https://nautilustrader.io/docs/latest/concepts/data/",
        )
    if dataset == "warehouse":
        if profile.category in {
            "MONTHLY_AVERAGE",
            "EQUITY_INDEX",
            "FREIGHT_INDEX",
            "GOVERNMENT_BOND",
        }:
            return Requirement(
                "NOT_APPLICABLE",
                "本类型不以商品仓单交割；商品仓单日报不适用",
                "https://tushare.pro/document/2?doc_id=140",
            )
        if profile.category != "PHYSICAL_COMMODITY":
            return Requirement("UNKNOWN", "尚未核实商品仓单是否适用于本类型", BASIC_REFERENCE)
    if dataset in {"holdings", "weekly_detail"}:
        if profile.category in {"UNKNOWN", "MONTHLY_AVERAGE", "FREIGHT_INDEX"}:
            return Requirement(
                "UNKNOWN",
                "需核实本类型的品种报告代码和披露范围；不盲用合约品种代码请求",
                (
                    "https://tushare.pro/document/2?doc_id=139"
                    if dataset == "holdings"
                    else "https://tushare.pro/document/2?doc_id=216"
                ),
            )
    return Requirement("REQUIRED", "本类型的必需数据，须核验实际记录与适用区间", BASIC_REFERENCE)


def record_checks(dataset: str, profile: ContractType) -> str:
    """Name only the missing evidence relevant to this required dataset."""
    if dataset.endswith("min"):
        return "待核实历史交易时段、供应商时间标签及无成交分钟规则，再逐时段核对记录"
    if dataset in {"week", "month"}:
        return "待核实原生周期标签、最后不完整周期及应有记录"
    if dataset in {"settlement", "limits"}:
        suffix = (
            "；月均价最终现金结算依据须单独核对" if profile.category == "MONTHLY_AVERAGE" else ""
        )
        return "待核实逐日应有记录、费率/保证金字段及交易后适用区间" + suffix
    if dataset in {"holdings", "warehouse", "weekly_detail"}:
        return "待核实本品种历史披露日、报告范围及应有记录；空响应不是不适用依据"
    return "待逐交易日核对实际记录；请求窗口连续不代表记录完整"
