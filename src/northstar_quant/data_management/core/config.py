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
    max_provider_response_bytes: int = Field(default=10_485_760, ge=1, le=104_857_600)
    max_provider_response_rows: int = Field(default=10_000, ge=1, le=250_000)
    provider_timeout_seconds: float = Field(default=15.0, gt=0, le=60.0)
    provider_retrieval_stale_after_seconds: int = Field(default=900, ge=60, le=86_400)


@lru_cache
def get_settings() -> Settings:
    """Build and cache the process-wide settings object."""

    return Settings()
