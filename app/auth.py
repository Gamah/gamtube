from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader
from app.config import get_settings

_header = APIKeyHeader(name="X-API-Key")


def require_api_key(key: str = Security(_header)) -> str:
    if key != get_settings().api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return key
