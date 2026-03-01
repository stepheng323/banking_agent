# pyright: reportUnusedImport=false
# pylint: disable=unused-import
"""Database connection and session management."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from shared.config.settings import settings

DATABASE_URL = settings.database_url

_engine = None
_AsyncSessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        if not DATABASE_URL:
            raise ValueError("DATABASE_URL environment variable is not set")
        # Ensure we use the async driver
        db_url = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
        if "postgresql+psycopg://" in db_url:  # Handle previous replacement if it existed or direct psycopg usage
            db_url = db_url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)

        _engine = create_async_engine(
            db_url,
            echo=False,
            pool_size=20,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=3600,
            pool_timeout=30,
        )
        print("✓ Async Database connection pool initialized")
    return _engine


def get_session_local():
    global _AsyncSessionLocal
    if _AsyncSessionLocal is None:
        _AsyncSessionLocal = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
    return _AsyncSessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting async database session."""
    session_local = get_session_local()
    async with session_local() as session:
        yield session


def get_db_session() -> AsyncSession:
    """Helper for non-generator usages (manual context management recommended)."""
    return get_session_local()()


async def drop_db():
    """Drop all tables including LangGraph checkpoint tables."""
    from sqlalchemy import text

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    print("🗑️  All database tables dropped")
