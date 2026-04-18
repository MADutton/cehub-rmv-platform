from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


def _normalize_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://") and "+asyncpg" not in url:
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


_db_url = _normalize_url(settings.database_url)
engine = create_async_engine(_db_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

_IS_POSTGRES = "postgresql" in _db_url or "asyncpg" in _db_url

# Each tuple: (table, column, postgres_type, sqlite_type)
# All new columns must be nullable so existing rows are unaffected.
_COLUMN_MIGRATIONS = [
    ("turns",    "prompt_delivered_at",   "TIMESTAMP WITH TIME ZONE", "DATETIME"),
    ("turns",    "response_submitted_at", "TIMESTAMP WITH TIME ZONE", "DATETIME"),
    ("sessions", "metadata",              "JSONB",                    "JSON"),
]


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def _apply_column_migrations(conn) -> None:
    """Idempotently add new nullable columns to existing tables."""
    for table, column, pg_type, sqlite_type in _COLUMN_MIGRATIONS:
        col_type = pg_type if _IS_POSTGRES else sqlite_type
        if _IS_POSTGRES:
            await conn.execute(text(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type}"
            ))
        else:
            try:
                await conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"
                ))
            except Exception:
                pass  # column already exists in SQLite


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _apply_column_migrations(conn)
