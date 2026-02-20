import pytest_asyncio
from httpx import AsyncClient

from tests.fixtures.users.admin import user


@pytest_asyncio.fixture
async def authenticated_user(
    client: AsyncClient,
    user,  # noqa: F811
) -> dict:
    """Authenticate user and return tokens."""
    login_data = {
        "username": user.username,
        "password": "admin123",
    }
    resp = await client.post("/auth/login", json=login_data)
    assert resp.status_code == 200
    return resp.json()
