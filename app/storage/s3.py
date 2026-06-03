from pathlib import Path
from app.storage.base import StorageBackend


class S3StorageBackend(StorageBackend):
    def save(self, source_path: Path, dest_key: str) -> str:
        raise NotImplementedError  # TODO: boto3

    def get_url(self, key: str) -> str:
        raise NotImplementedError  # TODO: boto3

    def delete(self, key: str) -> None:
        raise NotImplementedError  # TODO: boto3

    def get_local_path(self, key: str) -> Path | None:
        raise NotImplementedError  # TODO: boto3
