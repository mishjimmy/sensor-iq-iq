"""Runtime configuration, read from environment variables (or a .env file)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Grist
    grist_api_key: str = ""
    grist_server: str = ""  # e.g. https://eisv2.getgrist.com
    grist_doc_id: str = "hvoLt8XVgH1nGhrV7KAH6B"
    grist_sync_interval_s: float = 30
    grist_map_refresh_s: float = 600

    # Panels
    panels_file: str = "panels.yaml"
    poll_interval_s: float = 5
    poll_timeout_s: float = 3
    offline_after_failures: int = 3

    # History (charts on /stats)
    history_db: str = "history.db"
    history_days: int = 90

    # Web
    admin_token: str = ""
    host: str = "0.0.0.0"
    port: int = 8080

    @property
    def grist_enabled(self) -> bool:
        return bool(self.grist_api_key and self.grist_server and self.grist_doc_id)


@lru_cache
def get_settings() -> Settings:
    return Settings()
