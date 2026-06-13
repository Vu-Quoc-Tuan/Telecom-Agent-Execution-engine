from pathlib import Path
from functools import cached_property
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_FILE = BASE_DIR / ".env"

class LLMSettings(BaseModel):
    PROVIDER: str
    OPENAI_API_URL: str
    OPENAI_API_KEY: str
    ANTHROPIC_API_KEY: str
    MODEL_NAME: str
    TEMPERATURE: float

class AppDatabaseSettings(BaseModel):
    HOST: str
    PORT: int
    USER: str
    PASSWORD: str
    DB: str

class TelecomSandboxSettings(BaseModel):
    CLICKHOUSE_HOST: str
    CLICKHOUSE_PORT: int
    CLICKHOUSE_USER: str
    CLICKHOUSE_PASSWORD: str
    
    POSTGRES_HOST: str
    POSTGRES_PORT: int
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str

    HOST: str
    PORT: int
    USER: str
    PASSWORD: str

# The Main Settings class
class Settings(BaseSettings):
    PROVIDER: str = "openai"
    OPENAI_API_URL: str = "https://api.openai.com/v1"
    OPENAI_API_KEY: str = Field(default="mock-key", repr=False)
    ANTHROPIC_API_KEY: str = Field(default="mock-key", repr=False)
    MODEL_NAME: str = "gpt-4o"
    TEMPERATURE: float = 0.1

    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "your_secure_password"
    POSTGRES_DB: str = "telecom_agent"

    # Test
    CLICKHOUSE_HOST: str
    CLICKHOUSE_PORT: int = 8123
    CLICKHOUSE_USER: str
    CLICKHOUSE_PASSWORD: str = ""
    
    EXTERNAL_POSTGRES_HOST: str
    EXTERNAL_POSTGRES_PORT: int = 8010
    EXTERNAL_POSTGRES_USER: str
    EXTERNAL_POSTGRES_PASSWORD: str = ""
    EXTERNAL_POSTGRES_DATABASE: str = "postgres"

    SSH_HOST: str
    SSH_PORT: int = 2222
    SSH_USER: str
    SSH_PASSWORD: str = ""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @model_validator(mode="before")
    @classmethod
    def clean_empty_strings(cls, values: dict) -> dict:
        cleaned = {}
        for k, v in values.items():
            if isinstance(v, str) and v.strip() == "":
                continue
            cleaned[k] = v
        return cleaned

    @cached_property
    def llm(self) -> LLMSettings:
        return LLMSettings(
            PROVIDER=self.PROVIDER,
            OPENAI_API_URL=self.OPENAI_API_URL,
            OPENAI_API_KEY=self.OPENAI_API_KEY,
            ANTHROPIC_API_KEY=self.ANTHROPIC_API_KEY,
            MODEL_NAME=self.MODEL_NAME,
            TEMPERATURE=self.TEMPERATURE
        )

    @cached_property
    def app_db(self) -> AppDatabaseSettings:
        return AppDatabaseSettings(
            HOST=self.POSTGRES_HOST,
            PORT=self.POSTGRES_PORT,
            USER=self.POSTGRES_USER,
            PASSWORD=self.POSTGRES_PASSWORD,
            DB=self.POSTGRES_DB
        )

    @cached_property
    def telecom_sandbox(self) -> TelecomSandboxSettings:
        return TelecomSandboxSettings(
            CLICKHOUSE_HOST=self.CLICKHOUSE_HOST,
            CLICKHOUSE_PORT=self.CLICKHOUSE_PORT,
            CLICKHOUSE_USER=self.CLICKHOUSE_USER,
            CLICKHOUSE_PASSWORD=self.CLICKHOUSE_PASSWORD,
            POSTGRES_HOST=self.EXTERNAL_POSTGRES_HOST,
            POSTGRES_PORT=self.EXTERNAL_POSTGRES_PORT,
            POSTGRES_USER=self.EXTERNAL_POSTGRES_USER,
            POSTGRES_PASSWORD=self.EXTERNAL_POSTGRES_PASSWORD,
            POSTGRES_DB=self.EXTERNAL_POSTGRES_DATABASE,
            HOST=self.SSH_HOST,
            PORT=self.SSH_PORT,
            USER=self.SSH_USER,
            PASSWORD=self.SSH_PASSWORD
        )

settings = Settings()