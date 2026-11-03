from __future__ import annotations

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    value = os.getenv("DATABASE_URL", "sqlite:///./fossil_science_bowl_local.db").strip()
    if value.startswith("postgres://"):
        value = "postgresql+psycopg://" + value[len("postgres://"):]
    elif value.startswith("postgresql://"):
        value = "postgresql+psycopg://" + value[len("postgresql://"):]
    return value


_URL = database_url()
_engine_kwargs: dict = {"pool_pre_ping": True}
if _URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs.update({"pool_recycle": 300, "pool_size": 3, "max_overflow": 2})

engine = create_engine(_URL, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    # Import models before create_all so all tables are registered.
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
