import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture(scope="session")
async def app(engine) -> FastAPI:
    from app.application import get_production_app

    fastapi_app = get_production_app()
    return fastapi_app


@pytest_asyncio.fixture(scope="session")
async def client(app: FastAPI) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac
