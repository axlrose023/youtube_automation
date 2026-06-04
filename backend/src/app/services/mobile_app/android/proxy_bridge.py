from __future__ import annotations

import asyncio
import contextlib
import socket
import sys
import time
from asyncio.subprocess import DEVNULL
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse


@dataclass(frozen=True)
class AndroidHttpProxyBridgeHandle:
    process: asyncio.subprocess.Process
    upstream_url: str
    listen_host: str
    listen_port: int

    @property
    def emulator_proxy_url(self) -> str:
        return f"http://{self.listen_host}:{self.listen_port}"

    @property
    def host_proxy_url(self) -> str:
        """Host-side URL for processes running on the host machine (Playwright)."""
        return f"http://127.0.0.1:{self.listen_port}"


class AndroidHttpProxyBridge:
    # Android emulator reaches the host machine via 10.0.2.2, not 127.0.0.1
    _EMULATOR_HOST_GATEWAY = "10.0.2.2"

    async def start(self, upstream_url: str) -> AndroidHttpProxyBridgeHandle:
        listen_host = "0.0.0.0"
        listen_port = self._find_free_port()
        pproxy_url = _to_pproxy_url(upstream_url)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            _PPROXY_BRIDGE_SCRIPT,
            "-l",
            f"http://{listen_host}:{listen_port}",
            "-r",
            pproxy_url,
            stdout=DEVNULL,
            stderr=DEVNULL,
        )
        try:
            await self._wait_until_listening(process, "127.0.0.1", listen_port)
            return AndroidHttpProxyBridgeHandle(
                process=process,
                upstream_url=upstream_url,
                listen_host=self._EMULATOR_HOST_GATEWAY,
                listen_port=listen_port,
            )
        except Exception:
            with contextlib.suppress(Exception):
                process.terminate()
            raise

    async def stop(self, handle: AndroidHttpProxyBridgeHandle) -> None:
        process = handle.process
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
            await asyncio.wait_for(process.wait(), timeout=5)

    @staticmethod
    def _find_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    async def _wait_until_listening(
        self,
        process: asyncio.subprocess.Process,
        host: str,
        port: int,
    ) -> None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.returncode is not None:
                raise RuntimeError("Local HTTP proxy bridge exited before becoming ready")
            try:
                _reader, writer = await asyncio.open_connection(host, port)
            except OSError:
                await asyncio.sleep(0.2)
                continue
            writer.close()
            await writer.wait_closed()
            return
        raise RuntimeError("Timed out waiting for local HTTP proxy bridge")


def _to_pproxy_url(upstream_url: str) -> str:
    """Convert standard socks5://user:pass@host:port to pproxy's socks5://host:port#user:pass format."""
    parsed = urlparse(upstream_url)
    if parsed.scheme.startswith("socks5") and parsed.username:
        credentials = parsed.username
        if parsed.password:
            credentials = f"{parsed.username}:{parsed.password}"
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, credentials))
    return upstream_url


_PPROXY_BRIDGE_SCRIPT = """
import builtins
import sys

_real_import = builtins.__import__

def _guarded_import(name, *args, **kwargs):
    if name == "uvloop":
        raise ModuleNotFoundError("uvloop disabled for pproxy")
    return _real_import(name, *args, **kwargs)

builtins.__import__ = _guarded_import
sys.argv[0] = "pproxy"
from pproxy.server import main
main()
"""
