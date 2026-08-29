"""
ingress/core/config.py - Centralized configuration for the Ingress microservice.

Hardware analogy: this module is the chip's 'configuration register'.
All the circuitry (scouts, scrapers) reads its behavior from here.
Variables are injected from the environment, never hardcoded into the silicon.
"""

from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """
    Configuration for the Ingress microservice (Sensors).
    Reads variables from the .env file or the operating system environment.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Ignore undeclared environment variables
    )

    # --- Application ---
    app_env: str = Field(
        default="development",
        description="Execution environment: 'development', 'staging', 'production'.",
    )
    log_level: str = Field(
        default="INFO",
        description="Log level: DEBUG, INFO, WARNING, ERROR.",
    )

    # --- Redis (L1 SRAM for Ingress Cache) ---
    redis_host: str = Field(
        default="localhost",
        description="Redis server hostname.",
    )
    redis_port: int = Field(
        default=6379,
        description="Redis server port.",
    )

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Returns a singleton instance of Settings (EEPROM Boot).
    """
    return Settings()
