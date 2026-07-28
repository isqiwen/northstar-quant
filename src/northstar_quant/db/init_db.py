"""数据库初始化工具。"""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from northstar_quant.config.settings import get_settings
from northstar_quant.logging_.logger import get_logger

logger = get_logger(__name__, command="init-db")


def _redact_database_url(database_url: str) -> str:
    """返回可安全写入日志的数据库地址。"""

    try:
        return make_url(database_url).render_as_string(hide_password=True)
    except (ArgumentError, ValueError):
        return "<invalid-database-url>"


def _build_alembic_config() -> Config:
    """构建指向当前项目迁移目录的 Alembic 配置。"""

    settings = get_settings()
    config_path = settings.project_root / "alembic.ini"
    script_path = settings.project_root / "alembic"
    if not config_path.is_file() or not script_path.is_dir():
        raise FileNotFoundError(
            "未找到 Alembic 配置或迁移目录，无法初始化数据库："
            f"config={config_path}，scripts={script_path}"
        )

    config = Config(str(config_path))
    config.set_main_option("script_location", str(script_path))
    config.set_main_option(
        "sqlalchemy.url",
        settings.database_url.replace("%", "%%"),
    )
    return config


def init_db() -> None:
    """通过 Alembic 将数据库初始化或升级到当前 head。"""

    settings = get_settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    logger.info(
        "开始初始化数据库，database_url=%s",
        _redact_database_url(settings.database_url),
    )

    command.upgrade(_build_alembic_config(), "head")
    logger.info("数据库表结构已升级到当前 Alembic head")
