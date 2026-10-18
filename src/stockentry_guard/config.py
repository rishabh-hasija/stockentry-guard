"""Environment configuration for the outer composition layer."""

from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STOCKENTRY_", env_file=".env", extra="ignore")

    database_url: str = "sqlite+pysqlite:///./data/stockentry_guard.db"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma3:4b"
    ollama_timeout_seconds: float = Field(default=120, gt=0)

    max_image_bytes: int = Field(default=10 * 1024 * 1024, gt=0)

    duplicate_review_threshold: float = Field(default=0.60, gt=0, lt=1)
    duplicate_likely_threshold: float = Field(default=0.85, gt=0, le=1)

    @model_validator(mode="after")
    def ordered_duplicate_thresholds(self) -> Self:
        if self.duplicate_review_threshold >= self.duplicate_likely_threshold:
            raise ValueError("Duplicate review threshold must be below likely threshold")
        return self
