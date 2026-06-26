from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPOSITORY_ROOT = BACKEND_DIR.parent
ENV_FILE = REPOSITORY_ROOT / ".env"
EXTERNAL_ENV_FILE = REPOSITORY_ROOT / ".env.external"


class Settings(BaseSettings):
    ENVIRONMENT: str = "development"
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    PROVIDER: str = "openai"
    OPENAI_API_URL: str = "https://api.openai.com/v1"
    OPENAI_API_KEY: str = Field(default="", repr=False)
    ANTHROPIC_API_KEY: str = Field(default="", repr=False)
    MODEL_NAME: str = "gpt-4o"
    ANTHROPIC_MODEL_NAME: str = "claude-3-5-sonnet-20241022"
    TEMPERATURE: float = 0.1
    LLM_TIMEOUT_SECONDS: float = 60.0
    LLM_MAX_RETRIES: int = 2
    LLM_MAX_TOKENS: int = 1024
    AGENT_MAX_STEPS: int = 10
    LLM_DEFAULT_HEADERS: str = ""
    CONTEXT_WINDOW_TOKENS: int = 200_000
    CONTEXT_COMPACTION_TRIGGER_RATIO: float = 0.65
    CONTEXT_COMPACTION_TARGET_RATIO: float = 0.45

    DATABASE_URL: str = Field(default="", repr=False)
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = Field(default="postgres", repr=False)
    POSTGRES_DB: str = "telecom_agent"

    CHECKPOINTER_BACKEND: str = "postgres"
    CHECKPOINTER_DATABASE_URL: str = Field(default="", repr=False)

    CLICKHOUSE_HOST: str = ""
    CLICKHOUSE_PORT: int = 8123
    CLICKHOUSE_DATABASE: str = "alarm_data"
    CLICKHOUSE_USER: str = ""
    CLICKHOUSE_PASSWORD: str = Field(default="", repr=False)

    EXTERNAL_POSTGRES_HOST: str = ""
    EXTERNAL_POSTGRES_PORT: int = 5432
    EXTERNAL_POSTGRES_USER: str = ""
    EXTERNAL_POSTGRES_PASSWORD: str = Field(default="", repr=False)
    EXTERNAL_POSTGRES_DATABASE: str = "postgres"

    SSH_HOST: str = ""
    SSH_PORT: int = 22
    SSH_USER: str = ""
    SSH_PASSWORD: str = Field(default="", repr=False)
    SSH_TIMEOUT_SECONDS: int = 30
    SSH_ALLOWED_NODES: str = ""
    SSH_NODE_HOST_MAP: str = ""
    SSH_KNOWN_HOSTS: str = ""
    SSH_AUTO_ADD_HOST_KEYS: bool = False

    EXTERNAL_CONNECTOR_TIMEOUT_SECONDS: int = 15
    QUERY_MAX_RESULT_ROWS: int = 1000

    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = Field(default="", repr=False)
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"

    RUN_TIMEOUT_SECONDS: int = 3600
    RUN_TIMEOUT_SWEEPER_ENABLED: bool = True
    RUN_TIMEOUT_SWEEPER_INTERVAL_SECONDS: int = 60
    RUN_TIMEOUT_SWEEPER_LIMIT: int = 100

    model_config = SettingsConfigDict(
        env_file=[ENV_FILE, EXTERNAL_ENV_FILE],
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    @model_validator(mode="before")
    @classmethod
    def ignore_blank_environment_values(cls, values: object) -> object:
        if not isinstance(values, dict):
            return values
        return {
            key: value
            for key, value in values.items()
            if not (isinstance(value, str) and not value.strip())
        }

    @property
    def database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        user = quote_plus(self.POSTGRES_USER)
        password = quote_plus(self.POSTGRES_PASSWORD)
        return (
            f"postgresql://{user}:{password}@{self.POSTGRES_HOST}:"
            f"{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def checkpointer_database_url(self) -> str:
        return self.CHECKPOINTER_DATABASE_URL or self.database_url

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def llm_default_headers(self) -> dict[str, str]:
        if not self.LLM_DEFAULT_HEADERS:
            return {}
        try:
            parsed = json.loads(self.LLM_DEFAULT_HEADERS)
        except json.JSONDecodeError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        return {str(key): str(value) for key, value in parsed.items()}


settings = Settings()


def build_llm_gateway(configuration: Settings):
    from app.llm.anthropic_provider import AnthropicAdapter, AnthropicConfig
    from app.llm.gateway import LLMGateway
    from app.llm.openai_provider import OpenAICompatibleAdapter, OpenAICompatibleConfig

    adapters = []
    if configuration.OPENAI_API_KEY:
        adapters.append(
            OpenAICompatibleAdapter(
                OpenAICompatibleConfig(
                    model=configuration.MODEL_NAME,
                    api_key=configuration.OPENAI_API_KEY,
                    base_url=configuration.OPENAI_API_URL,
                    timeout_seconds=configuration.LLM_TIMEOUT_SECONDS,
                    max_retries=configuration.LLM_MAX_RETRIES,
                    default_max_tokens=configuration.LLM_MAX_TOKENS,
                    default_headers=configuration.llm_default_headers,
                )
            )
        )
    if configuration.ANTHROPIC_API_KEY:
        adapters.append(
            AnthropicAdapter(
                AnthropicConfig(
                    model=configuration.ANTHROPIC_MODEL_NAME,
                    api_key=configuration.ANTHROPIC_API_KEY,
                    timeout_seconds=configuration.LLM_TIMEOUT_SECONDS,
                    max_retries=configuration.LLM_MAX_RETRIES,
                    default_max_tokens=configuration.LLM_MAX_TOKENS,
                )
            )
        )

    default_provider = (
        configuration.PROVIDER
        if any(adapter.provider == configuration.PROVIDER for adapter in adapters)
        else None
    )
    return LLMGateway(adapters=adapters, default_provider=default_provider)


@lru_cache(maxsize=1)
def get_llm_gateway():
    return build_llm_gateway(settings)
