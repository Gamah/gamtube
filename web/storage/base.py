from abc import ABC, abstractmethod
from pathlib import Path


class StorageBackend(ABC):
    @abstractmethod
    def save(self, source_path: Path, dest_key: str) -> str: ...

    @abstractmethod
    def get_url(self, key: str) -> str: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def get_local_path(self, key: str) -> Path | None: ...
