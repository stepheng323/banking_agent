# pyright: reportUnusedImport=false
# pylint: disable=unused-import
"""Database connection and session management."""
import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from shared.database.models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "")

_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        if not DATABASE_URL:
            raise ValueError("DATABASE_URL environment variable is not set")
        db_url = DATABASE_URL.replace(
            "postgresql://", "postgresql+psycopg://", 1)
        _engine = create_engine(db_url, echo=False)
    return _engine


def get_session_local():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=get_engine())
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_session() -> Session:
    return get_session_local()()


def init_db():
    try:
        Base.metadata.create_all(bind=get_engine())
        print("✅ Database tables initialized")
    except Exception as e:
        print(f"⚠️  Database initialization error: {e}")
        raise


def drop_db():
    Base.metadata.drop_all(bind=get_engine())
    print("🗑️  Database tables dropped")
