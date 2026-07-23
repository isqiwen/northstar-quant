"""IBKR 服务的兼容入口。

IBKR 连接能力现归属 ``execution`` 基础设施层。保留此模块是为了兼容已有导入；
新代码应从 :mod:`northstar_quant.execution.ibkr_service` 导入。
"""

from northstar_quant.execution.ibkr_service import IBKRService

__all__ = ["IBKRService"]
