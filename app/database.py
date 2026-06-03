from functools import lru_cache
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Session


class Base(DeclarativeBase):
    pass


@lru_cache(maxsize=1)
def _engine():
    from app.config import get_settings
    s = get_settings()
    kw = {"connect_args": {"check_same_thread": False}} if "sqlite" in s.database_url else {}
    return create_engine(s.database_url, **kw)


@lru_cache(maxsize=1)
def _factory():
    return sessionmaker(autocommit=False, autoflush=False, bind=_engine())


def SessionLocal() -> Session:
    return _factory()()


def get_db():
    db = _factory()()
    try:
        yield db
    finally:
        db.close()
