import hashlib


def short_id_for(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:12]
