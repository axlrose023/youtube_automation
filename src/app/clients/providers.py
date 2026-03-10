

from collections.abc import AsyncIterator

import httpx
from dishka import Provider, Scope, provide

from app.clients.example_service import ExampleServiceClient
from app.settings import Config


class HttpClientsProvider(Provider):


    @provide(scope=Scope.APP)
    async def get_httpx_client(self) -> AsyncIterator[httpx.AsyncClient]:

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            http2=True,
            follow_redirects=True,
        ) as client:
            yield client

    @provide(scope=Scope.REQUEST)
    def get_example_service_client(
        self,
        client: httpx.AsyncClient,
        config: Config,
    ) -> ExampleServiceClient:

        return ExampleServiceClient(client, config)










