"""Example external service client implementation.

This is a template showing how to implement a client for external service.
Replace with your actual service integration.
"""

import httpx
from pydantic import BaseModel

from app.clients.base import HttpClient
from app.settings import Config


class ExampleServiceResponse(BaseModel):
    """Example response model from external service."""

    id: int
    name: str
    data: dict


class ExampleServiceClient(HttpClient):
    """Client for Example External Service API.

    Example usage in your service/handler:
        async def my_handler(example_client: ExampleServiceClient):
            result = await example_client.get_data(user_id=123)
            return result
    """

    def __init__(self, client: httpx.AsyncClient, config: Config):
        """Initialize Example Service client.

        :param client: httpx AsyncClient (injected by Dishka)
        :param config: Application config (injected by Dishka)
        """
        # Get configuration for this service
        # You should add these to your Config/Settings
        api_url = getattr(config, "example_service_url", "https://api.example.com")
        api_key = getattr(config, "example_service_api_key", "")

        super().__init__(
            client=client,
            base_url=api_url,
            default_timeout=30.0,
            default_headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    async def get_data(self, user_id: int) -> ExampleServiceResponse:
        """Get data from external service.

        :param user_id: User identifier
        :return: ExampleServiceResponse with data
        :raises HttpClientError: If request fails
        """
        response = await self.get(
            path=f"/api/users/{user_id}/data",
            params={"include": "details"},
        )

        return ExampleServiceResponse(**response.json())

    async def create_resource(
        self,
        name: str,
        metadata: dict,
    ) -> ExampleServiceResponse:
        """Create a new resource in external service.

        :param name: Resource name
        :param metadata: Additional metadata
        :return: Created resource data
        """
        response = await self.post(
            path="/api/resources",
            json={
                "name": name,
                "metadata": metadata,
            },
        )
        return ExampleServiceResponse(**response.json())

    async def update_resource(
        self,
        resource_id: int,
        data: dict,
    ) -> ExampleServiceResponse:
        """Update existing resource.

        :param resource_id: Resource identifier
        :param data: Update data
        :return: Updated resource data
        """
        response = await self.patch(
            path=f"/api/resources/{resource_id}",
            json=data,
        )
        return ExampleServiceResponse(**response.json())

    async def delete_resource(self, resource_id: int) -> None:
        """Delete a resource.

        :param resource_id: Resource identifier
        """
        await self.delete(path=f"/api/resources/{resource_id}")
