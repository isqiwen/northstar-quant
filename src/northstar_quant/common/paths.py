"""路径相关工具。"""

from pathlib import Path


def ensure_dir(path: str | Path) -> Path:
    """确保目录存在，不存在则自动创建。"""

    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p
