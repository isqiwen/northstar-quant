"""外部市场数据来源适配器。

本包只负责调用供应商并将其返回值转换为项目的标准行情表；下载发布、质量校验和
本地存储仍由 :mod:`northstar_quant.data_platform.sources.downloader`、``schema`` 与 ``storage`` 负责。
"""
