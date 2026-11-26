from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_migrations_dir() -> Path:
    repo_relative = Path(__file__).resolve().parent.parent / "migrations"
    if repo_relative.exists():
        return repo_relative
    return Path.cwd() / "migrations"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Life Annuity Ledger Reference API"
    app_version: str = "0.1.0"
    app_env: str = "development"
    log_level: str = "INFO"

    allowed_origins: str = "http://localhost:3000,http://localhost:5173"

    database_url: str = "sqlite:///./data/annuity_ledger.db"
    migrations_dir: Path = _default_migrations_dir()

    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    llm_timeout_seconds: float = 15.0
    llm_max_tokens: int = 220

    explain_rate_limit_per_minute: int = 10

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]
