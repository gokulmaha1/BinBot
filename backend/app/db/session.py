"""
BinBot AI Auto Mode — Async Database Session
PostgreSQL via SQLAlchemy 2.0 async engine.
"""

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
    AsyncEngine,
)
from sqlalchemy.orm import DeclarativeBase
from typing import AsyncGenerator

from app.config import settings


# ── Base Model ───────────────────────────────────────────────────
class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


# ── Engine & Session Factory ─────────────────────────────────────
engine_kwargs = {
    "echo": settings.DATABASE_ECHO,
    "pool_pre_ping": True,
    "pool_recycle": 3600,
}

if not settings.DATABASE_URL.startswith("sqlite"):
    engine_kwargs["pool_size"] = 20
    engine_kwargs["max_overflow"] = 10

engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    **engine_kwargs
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ── Dependency Injection ─────────────────────────────────────────
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields an async DB session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── Lifecycle ────────────────────────────────────────────────────
async def init_db() -> None:
    """Create all tables. Called on app startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Dispose engine. Called on app shutdown."""
    await engine.dispose()
