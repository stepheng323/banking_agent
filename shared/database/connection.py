# pyright: reportUnusedImport=false
# pylint: disable=unused-import
"""Database connection and session management."""

from collections.abc import AsyncGenerator

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from shared.config.settings import settings

DATABASE_URL = settings.database_url

_engine = None
_AsyncSessionLocal = None


def _build_async_db_url_and_connect_args(raw_database_url: str) -> tuple[str, dict[str, bool]]:
    """Normalize database URL and connect args for SQLAlchemy asyncpg."""
    db_url = raw_database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if "postgresql+psycopg://" in db_url:
        db_url = db_url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)

    connect_args: dict[str, bool] = {}

    if not db_url.startswith("postgresql+asyncpg://"):
        return db_url, connect_args

    parsed = make_url(db_url)
    query = dict(parsed.query)
    removed_params: list[str] = []

    sslmode = query.pop("sslmode", None)
    if sslmode is not None:
        removed_params.append("sslmode")
        connect_args["ssl"] = str(sslmode).lower() != "disable"

    # asyncpg does not accept channel_binding in connect kwargs.
    if query.pop("channel_binding", None) is not None:
        removed_params.append("channel_binding")

    if removed_params:
        db_url = parsed.set(query=query).render_as_string(hide_password=False)
        print("⚙️  Normalized DATABASE_URL for asyncpg; removed unsupported params: " + ", ".join(removed_params))

    return db_url, connect_args


def get_engine():
    global _engine
    if _engine is None:
        if not DATABASE_URL:
            raise ValueError("DATABASE_URL environment variable is not set")
        db_url, connect_args = _build_async_db_url_and_connect_args(DATABASE_URL)

        _engine = create_async_engine(
            db_url,
            connect_args=connect_args,
            echo=False,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
            pool_recycle=3600,
            pool_timeout=settings.db_pool_timeout,
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
