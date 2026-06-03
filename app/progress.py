_store: dict[str, dict] = {}


def set_progress(short_id: str, stage: str, pct: float | None) -> None:
    _store[short_id] = {"stage": stage, "pct": round(pct, 1) if pct is not None else None}


def get_progress(short_id: str) -> dict | None:
    return _store.get(short_id)
