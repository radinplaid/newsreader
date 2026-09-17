import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    nr_db_path: str = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "data", "newsreader.db"
    )
    nr_web_dir: str = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "web"
    )
    nr_refresh_interval_min: float = 0.0
    nr_min_refresh_minutes: float = 15.0
    nr_max_concurrency: int = 24
    nr_request_timeout: float = 30.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
