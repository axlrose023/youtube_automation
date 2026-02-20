import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestRefreshToken:
    endpoint = "/auth/refresh"

    async def test_refresh_token_success(
        self,
        client: AsyncClient,
        authenticated_user: dict,
    ):
        refresh_token = authenticated_user["refresh_token"]
        payload = {"refresh_token": refresh_token}

        resp = await client.post(self.endpoint, json=payload)
        assert resp.status_code == 200

        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert "token_type" in data
        assert data["token_type"] == "bearer"

    async def test_refresh_token_missing_token(
        self,
        client: AsyncClient,
    ):
        payload = {}

        resp = await client.post(self.endpoint, json=payload)

        assert resp.status_code == 422

    async def test_refresh_token_invalid_token(
        self,
        client: AsyncClient,
    ):
        payload = {"refresh_token": "invalid_token"}

        resp = await client.post(self.endpoint, json=payload)

        assert resp.status_code == 401

    async def test_refresh_token_expired_token(
        self,
        client: AsyncClient,
    ):
        expired_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwidHlwZSI6InJlZnJlc2giLCJleHAiOjE1MTYyMzkwMjJ9.expired"
        payload = {"refresh_token": expired_token}

        resp = await client.post(self.endpoint, json=payload)

        assert resp.status_code == 401
