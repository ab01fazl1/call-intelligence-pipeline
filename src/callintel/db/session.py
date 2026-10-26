from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(url: str, *, pool_size: int = 10) -> AsyncEngine:
    return create_async_engine(url, pool_size=pool_size, max_overflow=pool_size, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def asyncpg_dsn(sqlalchemy_url: str) -> str:
    """postgresql+asyncpg://... -> postgresql://... for code that talks to asyncpg directly."""
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)
