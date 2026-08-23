"""YAML 配置加载器。"""

from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: str | Path) -> dict[str, Any]:
    """读取 YAML 配置文件并返回字典。"""

    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
