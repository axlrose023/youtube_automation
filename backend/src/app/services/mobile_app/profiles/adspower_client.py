from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from app.settings import AdsPowerConfig


@dataclass(frozen=True)
class AdsPowerProfileProxy:
    profile_id: str
    country_code: str | None
    proxy_type: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None

    @property
    def upstream_url(self) -> str:
        auth = ""
        if self.username:
            user = quote(self.username, safe="")
            password = quote(self.password or "", safe="")
            auth = f"#{user}:{password}"
        return f"{self.proxy_type}://{self.host}:{self.port}{auth}"


class AdsPowerProfileClient:
    def __init__(self, config: AdsPowerConfig) -> None:
        self._base_url = config.base_url.rstrip("/")
        self._api_key = (config.api_key or "").strip()

    async def fetch_proxy(self, profile_id: str) -> AdsPowerProfileProxy | None:
        if not self._api_key:
            raise RuntimeError("AdsPower api_key is not configured")

        last_error: Exception | None = None
        for base_url in self._candidate_base_urls():
            try:
                profile = await self._fetch_profile_from_base(base_url, profile_id)
            except Exception as exc:
                last_error = exc
                continue
            if profile is None:
                continue

            proxy_data = profile.get("user_proxy_config") or {}
            if not isinstance(proxy_data, dict):
                return None

            proxy_soft = str(proxy_data.get("proxy_soft") or "").strip().casefold()
            if proxy_soft in {"", "no_proxy"}:
                return None

            host = str(proxy_data.get("proxy_host") or "").strip()
            port_raw = str(proxy_data.get("proxy_port") or "").strip()
            proxy_type = str(proxy_data.get("proxy_type") or "http").strip().casefold()
            if not host or not port_raw:
                raise RuntimeError(f"AdsPower profile {profile_id} has incomplete proxy config")
            try:
                port = int(port_raw)
            except ValueError as exc:
                raise RuntimeError(
                    f"AdsPower profile {profile_id} has invalid proxy port: {port_raw}"
                ) from exc

            return AdsPowerProfileProxy(
                profile_id=profile_id,
                country_code=_as_optional_str(profile.get("ip_country")),
                proxy_type=proxy_type,
                host=host,
                port=port,
                username=_as_optional_str(proxy_data.get("proxy_user")),
                password=_as_optional_str(proxy_data.get("proxy_password")),
            )

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"AdsPower profile {profile_id} was not found")

    async def _fetch_profile_from_base(
        self,
        base_url: str,
        profile_id: str,
    ) -> dict[str, object] | None:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{base_url}/api/v1/user/list",
                params={"page": 1, "page_size": 100},
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=20.0,
            )
            response.raise_for_status()
            payload = response.json()

        if payload.get("code") != 0:
            raise RuntimeError(
                f"AdsPower user/list failed for {base_url}: {payload.get('msg')}"
            )
        data = payload.get("data") or {}
        items = data.get("list") or []
        if not isinstance(items, list):
            return None
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("user_id") or "").strip() == profile_id:
                return item
        return None

    def _candidate_base_urls(self) -> list[str]:
        candidates = [self._base_url]
        parsed = urlsplit(self._base_url)
        if parsed.hostname == "host.docker.internal":
            host = "local.adspower.net"
            netloc = f"{host}:{parsed.port}" if parsed.port else host
            candidates.append(
                urlunsplit(
                    (
                        parsed.scheme,
                        netloc,
                        parsed.path,
                        parsed.query,
                        parsed.fragment,
                    )
                ).rstrip("/")
            )
        unique: list[str] = []
        for candidate in candidates:
            normalized = candidate.rstrip("/")
            if normalized and normalized not in unique:
                unique.append(normalized)
        return unique


def _as_optional_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
