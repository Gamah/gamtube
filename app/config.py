from functools import lru_cache
from typing import Literal
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./gamtube.db"
    media_root: str = "./media"
    media_base_url: str = "http://localhost/media"
    storage_backend: Literal["local", "s3"] = "local"
    base_url: str = "http://localhost"
    temp_dir: str | None = None
    video_ttl_hours: int = 24

    s3_bucket: str = ""
    s3_region: str = "us-east-1"
    s3_access_key: str = ""
    s3_secret_key: str = ""

    model_config = {"env_file": ".env"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def get_storage():
    s = get_settings()
    if s.storage_backend == "local":
        from app.storage.local import LocalStorageBackend
        return LocalStorageBackend(s.media_root, s.media_base_url)
    from app.storage.s3 import S3StorageBackend
    return S3StorageBackend()
