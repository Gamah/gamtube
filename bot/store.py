"""In-memory store for videos waiting to be fetched by Discord.

Nothing here touches disk. An item lives for `ttl_seconds` and the store as a
whole stays under `max_bytes`, oldest evicted first. That is the entire
persistence model: a restart loses everything, and every link the bot has
posted goes dead with it.

**This is the load-bearing assumption of the design**: that once Discord has
unfurled a link it holds its own copy, so the link may expire. If Discord
instead proxies each view from the origin, an evicted item means a dead embed
in channel history. Raise `LINK_TTL_SECONDS` if that turns out to be the case.

No discord.py, no FastAPI — the clock is injectable so the TTL and eviction
behaviour can be tested without sleeping.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class Item:
    data: bytes
    content_type: str
    title: str | None = None
    width: int | None = None
    height: int | None = None
    duration: int | None = None
    source_url: str | None = None
    created: float = 0.0
    fetches: int = field(default=0)

    @property
    def size(self) -> int:
        return len(self.data)


class MemoryStore:
    def __init__(
        self,
        ttl_seconds: float,
        max_bytes: int,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_bytes
        self._now = now
        self._lock = threading.Lock()
        # Insertion-ordered, so the oldest live item is always the first key.
        self._items: dict[str, Item] = {}

    def put(self, key: str, item: Item) -> None:
        item.created = self._now()
        with self._lock:
            self._items.pop(key, None)  # re-insert so ordering reflects recency
            self._items[key] = item
            self._prune_locked()
            while self._items and self._total_locked() > self._max:
                self._items.pop(next(iter(self._items)))

    def get(self, key: str) -> Item | None:
        with self._lock:
            self._prune_locked()
            item = self._items.get(key)
            if item is not None:
                item.fetches += 1
            return item

    def prune(self) -> int:
        with self._lock:
            return self._prune_locked()

    def total_bytes(self) -> int:
        with self._lock:
            return self._total_locked()

    def __len__(self) -> int:
        with self._lock:
            self._prune_locked()
            return len(self._items)

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

    # --- internals; caller holds the lock ---

    def _total_locked(self) -> int:
        return sum(i.size for i in self._items.values())

    def _prune_locked(self) -> int:
        if self._ttl <= 0:
            return 0
        cutoff = self._now() - self._ttl
        dead = [k for k, i in self._items.items() if i.created <= cutoff]
        for k in dead:
            del self._items[k]
        return len(dead)
