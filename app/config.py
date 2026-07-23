from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    external_base_url: str = "http://91.199.149.128:18001"
    precompute_on_download: bool = True
    data_dir: Path = Path("data")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
