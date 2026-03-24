

import httpx
from pydantic import BaseModel

from app.clients.base import HttpClient
from app.settings import Config


class ExampleServiceResponse(BaseModel):


    id: int
    name: str
    data: dict


class ExampleServiceClient(HttpClient):


    def __init__(self, client: httpx.AsyncClient, config: Config):



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

        response = await self.patch(
            path=f"/api/resources/{resource_id}",
            json=data,
        )
        return ExampleServiceResponse(**response.json())

    async def delete_resource(self, resource_id: int) -> None:

        await self.delete(path=f"/api/resources/{resource_id}")
