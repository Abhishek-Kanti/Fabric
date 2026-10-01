from collections.abc import AsyncGenerator, Generator
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app


@pytest.fixture
def test_settings() -> Settings:
    """Fixture providing testing settings."""
    return Settings(
        app_name="Company Brain (Test)",
        environment="testing",
        debug=True,
        log_level="WARNING",
    )


@pytest.fixture
def client(test_settings: Settings) -> Generator[TestClient, None, None]:
    """Fixture providing a TestClient configured with test settings."""
    app = create_app(settings=test_settings)
    app.dependency_overrides[get_settings] = lambda: test_settings
    with TestClient(app) as test_client:
        yield test_client


@pytest_asyncio.fixture(scope="session")
async def db_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Fixture providing an async SQLAlchemy engine connected to PostgreSQL test database."""
    settings = get_settings()
    engine = create_async_engine(
        settings.async_test_database_url,
        echo=False,
        poolclass=NullPool,
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Fixture providing an isolated AsyncSession wrapped in a rolled-back transaction."""
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
        try:
            yield session
        finally:
            await session.close()
            if connection.in_transaction():
                await transaction.rollback()
