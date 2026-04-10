from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str
    database_url: str = "sqlite+aiosqlite:///./sessions.db"
    secret_key: str = "dev-secret-key"
    openai_model: str = "gpt-4o"

    # Data directories
    assigned_case_dir: str = "data/assigned-case-rmv"
    case_based_dir: str = "data/case-based-rmv"
    mastery_module_dir: str = "data/mastery-module-rmv"


settings = Settings()
