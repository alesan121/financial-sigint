"""
orchestrator/core/config.py - Plant EEPROM v4.2.
Registry of system voltages and constants.
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class OrchestratorSettings(BaseSettings):
    # --- INFERENCE BUS ---
    ollama_base_url: str = Field(default="http://host.docker.internal:11434")
    router_model: str = Field(default="qwen3:8b")
    tool_model: str = Field(default="qwen3:8b")
    reasoning_model: str = Field(default="phi4-mini-reasoning")

    # --- EXECUTION BUS (Alpaca) ---
    alpaca_api_key: SecretStr = Field(default=SecretStr(""))
    alpaca_secret_key: SecretStr = Field(default=SecretStr(""))
    alpaca_paper: bool = Field(default=True)

    # --- TELEMETRY BUS ---
    telegram_bot_token: SecretStr = Field(default=SecretStr(""))
    telegram_chat_id: str = Field(default="")
    telemetry_db_path: str = Field(default="/app/data/telemetry.db")

    # --- DSP PARAMETERS ---
    virtual_balance_usd: float = Field(default=100_000.0)
    min_allocation_usd: float = Field(default=1000.0)
    kelly_divisor: float = Field(default=4.0)
    max_kelly_fraction: float = Field(default=0.10)
    min_judge_confidence: float = Field(default=0.60)
    min_pop_threshold: float = Field(default=0.50)
    target_profit_usd: float = Field(default=1000.0)
    max_cpu_threads: int = Field(default=8)
    ollama_num_ctx: int = Field(default=4096)
    ollama_num_parallel: int = Field(default=1)

    # --- SAFETY BREAKERS (Fly-by-Wire) ---
    dry_run_mode: bool = Field(default=True)
    hitl_enabled: bool = Field(default=True)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache(maxsize=1)
def get_orchestrator_settings() -> OrchestratorSettings:
    return OrchestratorSettings()
