from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


API_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_DIR / ".env", API_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    demo_mode: bool = True
    public_base_url: str = "http://127.0.0.1:5173"
    database_url: str = f"sqlite:///{(API_DIR / 'creative_museum.db').as_posix()}"
    openai_api_key: str = ""
    openai_model: str = "gpt-6-luna"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_reasoning_effort: str = "low"
    openai_max_output_tokens: int = 1600
    chat_max_message_chars: int = 4000
    admin_username: str = "admin"
    admin_password: str = "konkursadmin"
    session_secret: str = "local-development-only-change-before-production"
    cookie_secure: bool = False
    active_contest_season: str = ""
    display_timezone: str = "Europe/Moscow"
    support_email: str = "creativemuseum@futuretime.ru"
    support_phone: str = "8 800 600-79-31"
    daily_api_budget_usd: float = 5.0
    monthly_api_budget_usd: float = 50.0
    export_ttl_hours: int = 24

    @property
    def knowledge_path(self) -> Path:
        return PROJECT_DIR / "knowledge" / "qa_source.txt"

    @property
    def general_knowledge_path(self) -> Path:
        return PROJECT_DIR / "knowledge" / "general_source.txt"

    @property
    def export_path(self) -> Path:
        return API_DIR / "private_storage" / "exports"


@lru_cache
def get_settings() -> Settings:
    return Settings()
