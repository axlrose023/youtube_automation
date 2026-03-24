

import logging
from typing import Any

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class HttpClientError(Exception):


    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_body: Any | None = None,
    ):
        self.message = message
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(message)


class HttpClient:


    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str | None = None,
        default_timeout: float = 30.0,
        default_headers: dict[str, str] | None = None,
    ):

        self.client = client
        self.base_url = base_url.rstrip("/") if base_url else ""
        self.default_timeout = default_timeout
        self.default_headers = default_headers or {}

    def _build_url(self, path: str) -> str:

        path = path.lstrip("/")
        if self.base_url:
            return f"{self.base_url}/{path}"
        return path

    def _merge_headers(self, headers: dict[str, str] | None) -> dict[str, str]:

        merged = self.default_headers.copy()
        if headers:
            merged.update(headers)
        return merged

    async def _make_request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:


        if "timeout" not in kwargs:
            kwargs["timeout"] = self.default_timeout


        if "headers" in kwargs:
            kwargs["headers"] = self._merge_headers(kwargs["headers"])
        elif self.default_headers:
            kwargs["headers"] = self.default_headers

        try:
            logger.debug(
                "Making %s request to %s",
                method.upper(),
                url,
                extra={"method": method, "url": url, "kwargs": kwargs},
            )

            response = await self.client.request(method, url, **kwargs)

            logger.debug(
                "Received response: %s %s",
                response.status_code,
                url,
                extra={
                    "status_code": response.status_code,
                    "url": url,
                    "method": method,
                },
            )

            response.raise_for_status()
            return response

        except httpx.HTTPStatusError as e:
            logger.error(
                "HTTP error: %s %s - Status %s",
                method.upper(),
                url,
                e.response.status_code,
                extra={
                    "status_code": e.response.status_code,
                    "response_body": e.response.text,
                },
            )
            raise HttpClientError(
                message=f"HTTP {e.response.status_code}: {e.response.text}",
                status_code=e.response.status_code,
                response_body=e.response.text,
            ) from e

        except httpx.TimeoutException as e:
            logger.error("Request timeout: %s %s", method.upper(), url)
            raise HttpClientError(
                message=f"Request timeout: {url}",
            ) from e

        except httpx.RequestError as e:
            logger.error(
                "Request error: %s %s - %s",
                method.upper(),
                url,
                str(e),
            )
            raise HttpClientError(
                message=f"Request error: {str(e)}",
            ) from e

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:

        url = self._build_url(path)
        return await self._make_request(
            "GET", url, params=params, headers=headers, **kwargs
        )

    async def post(
        self,
        path: str,
        json: dict[str, Any] | BaseModel | None = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:

        url = self._build_url(path)


        if isinstance(json, BaseModel):
            json = json.model_dump(mode="json")

        return await self._make_request(
            "POST", url, json=json, data=data, headers=headers, **kwargs
        )

    async def put(
        self,
        path: str,
        json: dict[str, Any] | BaseModel | None = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:

        url = self._build_url(path)

        if isinstance(json, BaseModel):
            json = json.model_dump(mode="json")

        return await self._make_request(
            "PUT", url, json=json, data=data, headers=headers, **kwargs
        )

    async def patch(
        self,
        path: str,
        json: dict[str, Any] | BaseModel | None = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:

        url = self._build_url(path)

        if isinstance(json, BaseModel):
            json = json.model_dump(mode="json")

        return await self._make_request(
            "PATCH", url, json=json, data=data, headers=headers, **kwargs
        )

    async def delete(
        self,
        path: str,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:

        url = self._build_url(path)
        return await self._make_request("DELETE", url, headers=headers, **kwargs)
