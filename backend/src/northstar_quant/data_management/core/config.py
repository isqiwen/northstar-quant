"""Runtime configuration for Quant Data Hub."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Bounded acquisition settings for the application's data implementation."""

    model_config = SettingsConfigDict(
        env_prefix="NORTHSTAR_",
        extra="ignore",
    )

    max_csv_bytes: int = Field(default=52_428_800, ge=1, le=1_073_741_824)
    max_csv_rows: int = Field(default=250_000, ge=1, le=250_000)
    max_csv_field_bytes: int = Field(default=16_384, ge=1_024, le=1_048_576)
    max_parquet_bytes: int = Field(default=52_428_800, ge=1, le=1_073_741_824)
    max_parquet_rows: int = Field(default=250_000, ge=1, le=250_000)
    max_parquet_field_bytes: int = Field(default=16_384, ge=1_024, le=1_048_576)
    max_parquet_uncompressed_bytes: int = Field(default=104_857_600, ge=1, le=2_147_483_648)


@lru_cache
def get_settings() -> Settings:
    """Build and cache the process-wide settings object."""

    return Settings()
