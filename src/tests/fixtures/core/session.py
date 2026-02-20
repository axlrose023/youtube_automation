import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.database.uow import UnitOfWork

SHARED_DSN = "sqlite+aiosqlite:///file::memory:?cache=shared&uri=true"


@pytest.fixture(scope="session", autouse=True)
def override_database_in_settings():
    from app import settings

    def test_database_url(self):
        return SHARED_DSN

    settings.Config.database_url = property(test_database_url)


@pytest.fixture(scope="session")
async def engine(override_database_in_settings) -> AsyncEngine:
    test_engine = create_async_engine(
        SHARED_DSN,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
        echo=False,
    )

    @event.listens_for(test_engine.sync_engine, "connect")
    def _enable_sqlite_fks(dbapi_conn, _) -> None:
        dbapi_conn.execute("PRAGMA foreign_keys = ON")

    async def _prepare() -> None:
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    await _prepare()
    return test_engine


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncSession:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def uow(session: AsyncSession) -> UnitOfWork:
    async with UnitOfWork(session) as uow:
        yield uow
