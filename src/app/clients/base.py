"""Base HTTP client for external services."""

import logging
from typing import Any

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class HttpClientError(Exception):
    """Base exception for HTTP client errors."""

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
    """Base HTTP client with common functionality for external API integrations.

    Features:
    - Automatic error handling
    - Request/response logging
    - Base URL management
    - Common headers
    - Timeout configuration

    Usage:
        class MyServiceClient(BaseHttpClient):
            def __init__(self, client: httpx.AsyncClient, config: MyServiceConfig):
                super().__init__(
                    client=client,
                    base_url=str(config.api_url),
                    default_timeout=30.0,
                )

            async def get_data(self, id: int) -> MyDataModel:
                response = await self.get(f"/data/{id}")
                return MyDataModel(**response.json())
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str | None = None,
        default_timeout: float = 30.0,
        default_headers: dict[str, str] | None = None,
    ):
        """Initialize base HTTP client.

        :param client: httpx AsyncClient instance (managed by DI container)
        :param base_url: Base URL for all requests
        :param default_timeout: Default timeout in seconds
        :param default_headers: Headers to include in all requests
        """
        self.client = client
        self.base_url = base_url.rstrip("/") if base_url else ""
        self.default_timeout = default_timeout
        self.default_headers = default_headers or {}

    def _build_url(self, path: str) -> str:
        """Build full URL from base URL and path."""
        path = path.lstrip("/")
        if self.base_url:
            return f"{self.base_url}/{path}"
        return path

    def _merge_headers(self, headers: dict[str, str] | None) -> dict[str, str]:
        """Merge default headers with request-specific headers."""
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
        """Make HTTP request with error handling and logging."""
        # Set default timeout if not provided
        if "timeout" not in kwargs:
            kwargs["timeout"] = self.default_timeout

        # Merge headers
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
        """Make GET request.

        :param path: API endpoint path
        :param params: Query parameters
        :param headers: Request headers
        :param kwargs: Additional httpx request parameters
        :return: HTTP response
        """
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
        """Make POST request.

        :param path: API endpoint path
        :param json: JSON body (dict or Pydantic model)
        :param data: Form data
        :param headers: Request headers
        :param kwargs: Additional httpx request parameters
        :return: HTTP response
        """
        url = self._build_url(path)

        # Convert Pydantic model to dict if needed
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
        """Make PUT request.

        :param path: API endpoint path
        :param json: JSON body (dict or Pydantic model)
        :param data: Form data
        :param headers: Request headers
        :param kwargs: Additional httpx request parameters
        :return: HTTP response
        """
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
        """Make PATCH request.

        :param path: API endpoint path
        :param json: JSON body (dict or Pydantic model)
        :param data: Form data
        :param headers: Request headers
        :param kwargs: Additional httpx request parameters
        :return: HTTP response
        """
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
        """Make DELETE request.

        :param path: API endpoint path
        :param headers: Request headers
        :param kwargs: Additional httpx request parameters
        :return: HTTP response
        """
        url = self._build_url(path)
        return await self._make_request("DELETE", url, headers=headers, **kwargs)
