import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

project_root = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, project_root)

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from shared.config.settings import settings  # noqa: E402
from shared.database.models import Base  # noqa: E402

config = context.config

database_url = settings.database_url
if database_url:
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)
else:
    section = config.get_section(config.config_ini_section, {})
    if "sqlalchemy.url" not in section:
        print("⚠️  DATABASE_URL environment variable not set")
        print("   Set DATABASE_URL or configure it in alembic.ini")

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    configuration = config.get_section(config.config_ini_section, {})

    if "url" not in configuration or not configuration.get("url"):
        database_url = os.getenv("DATABASE_URL")
        if database_url:
            database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
            configuration["url"] = database_url
        else:
            raise ValueError("DATABASE_URL environment variable is not set")

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
