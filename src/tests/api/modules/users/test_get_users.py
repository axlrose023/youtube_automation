import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestGetUsers:
    endpoint = "/users"

    async def test_get_users_success(
        self,
        client: AsyncClient,
        authenticated_user: dict,
    ):
        access_token = authenticated_user["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}

        resp = await client.get(
            self.endpoint, headers=headers, params={"page": 1, "page_size": 10}
        )

        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "items" in data
        assert "page" in data
        assert "page_size" in data

    async def test_get_users_unauthorized(
        self,
        client: AsyncClient,
    ):
        resp = await client.get(self.endpoint, params={"page": 1, "page_size": 10})

        assert resp.status_code == 401
