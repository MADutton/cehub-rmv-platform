from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str
    database_url: str = "sqlite+aiosqlite:///./sessions.db"
    secret_key: str = "dev-secret-key"
    openai_model: str = "gpt-4o"
    cases_dir: str = "assigned-case-rmv/cases"
    product_dir: str = "assigned-case-rmv/product"
    prompts_dir: str = "assigned-case-rmv/prompts"


settings = Settings()
