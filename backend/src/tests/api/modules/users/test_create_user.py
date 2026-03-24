import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestCreateUser:
    endpoint = "/users"

    async def test_create_user_success(
        self,
        client: AsyncClient,
    ):
        payload = {
            "username": "testuser",
            "password": "testpass123",
        }

        resp = await client.post(self.endpoint, json=payload)

        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        assert data["username"] == "testuser"

    async def test_create_user_empty_username(
        self,
        client: AsyncClient,
    ):
        payload = {
            "username": "",
            "password": "testpass123",
        }

        resp = await client.post(self.endpoint, json=payload)

        assert resp.status_code == 422

    async def test_create_user_empty_password(
        self,
        client: AsyncClient,
    ):
        payload = {
            "username": "testuser",
            "password": "",
        }

        resp = await client.post(self.endpoint, json=payload)

        assert resp.status_code == 422
