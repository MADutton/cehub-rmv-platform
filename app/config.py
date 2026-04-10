from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str
    database_url: str = "sqlite+aiosqlite:///./sessions.db"
    secret_key: str = "dev-secret-key"
    claude_model: str = "claude-sonnet-4-6"
    cases_dir: str = "assigned-case-rmv/cases"
    product_dir: str = "assigned-case-rmv/product"
    prompts_dir: str = "assigned-case-rmv/prompts"


settings = Settings()
