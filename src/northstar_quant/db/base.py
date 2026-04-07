"""SQLAlchemy 基类定义。"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有 ORM 模型的共同基类。"""
