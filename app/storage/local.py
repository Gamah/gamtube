import shutil
from pathlib import Path
from app.storage.base import StorageBackend


class LocalStorageBackend(StorageBackend):
    def __init__(self, media_root: str, media_base_url: str):
        self.root = Path(media_root)
        self.base_url = media_base_url.rstrip("/")
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, source_path: Path, dest_key: str) -> str:
        shutil.move(str(source_path), str(self.root / dest_key))
        return dest_key

    def get_url(self, key: str) -> str:
        return f"{self.base_url}/{key}"

    def delete(self, key: str) -> None:
        (self.root / key).unlink(missing_ok=True)

    def get_local_path(self, key: str) -> Path | None:
        return self.root / key
