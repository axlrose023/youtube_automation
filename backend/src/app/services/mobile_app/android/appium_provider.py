from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time
from asyncio.subprocess import DEVNULL
from dataclasses import dataclass

import anyio
import httpx

from app.settings import AndroidAppConfig

from .errors import AndroidAppiumError
from .tooling import build_android_runtime_env, require_tool_path


@dataclass(frozen=True)
class AndroidDriverHandle:
    driver: object
    server_url: str
    started_local_server: bool


class LocalAppiumServer:
    def __init__(self, config: AndroidAppConfig) -> None:
        self._config = config
        self._process: asyncio.subprocess.Process | None = None
        self._log_path = Path("/tmp/android_appium_server.log")

    async def start(self, *, force_restart: bool = False) -> bool:
        if force_restart:
            await self.stop()
            await anyio.to_thread.run_sync(self._terminate_listeners_sync)
        elif await self._is_ready():
            return False
        if not self._config.manage_appium_server:
            raise AndroidAppiumError(
                f"Appium server is not reachable at {self._config.appium_server_url}"
            )

        appium_bin = require_tool_path("appium")
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = self._log_path.open("a", encoding="utf-8")
        self._process = await asyncio.create_subprocess_exec(
            appium_bin,
            "--address",
            self._config.appium_host,
            "--port",
            str(self._config.appium_port),
            "--base-path",
            self._config.appium_base_path,
            env=build_android_runtime_env(),
            stdout=log_handle,
            stderr=log_handle,
        )
        await self._wait_until_ready()
        return True

    async def stop(self) -> None:
        if self._process is None:
            return
        self._process.terminate()
        try:
            with contextlib.suppress(ProcessLookupError):
                await asyncio.wait_for(self._process.wait(), timeout=10)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                self._process.kill()
            with contextlib.suppress(ProcessLookupError, TimeoutError):
                await asyncio.wait_for(self._process.wait(), timeout=5)
        self._process = None

    async def _wait_until_ready(self) -> None:
        for _ in range(30):
            if self._process is not None and self._process.returncode is not None:
                if await self._is_ready():
                    self._process = None
                    return
                raise AndroidAppiumError(
                    f"Appium exited early with code {self._process.returncode}: {self._tail_log()}"
                )
            if await self._is_ready():
                return
            await asyncio.sleep(1)
            if self._process is not None and self._process.returncode is not None:
                if await self._is_ready():
                    self._process = None
                    return
                raise AndroidAppiumError(
                    f"Appium exited early with code {self._process.returncode}: {self._tail_log()}"
                )
        raise AndroidAppiumError(
            f"Timed out waiting for Appium server at {self._config.appium_server_url}"
        )

    def _tail_log(self) -> str:
        if not self._log_path.exists():
            return "<no appium log>"
        lines = self._log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return " | ".join(lines[-20:]) or "<empty appium log>"

    async def _is_ready(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(f"{self._config.appium_server_url.rstrip('/')}/status")
            return response.status_code == 200
        except Exception:
            return False

    def _terminate_listeners_sync(self) -> None:
        lsof_bin = shutil.which("lsof")
        if not lsof_bin:
            return

        result = subprocess.run(
            [
                lsof_bin,
                "-ti",
                f"tcp:{self._config.appium_port}",
                "-sTCP:LISTEN",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        pids = [
            int(line.strip())
            for line in (result.stdout or "").splitlines()
            if line.strip().isdigit()
        ]
        if not pids:
            return

        current_pid = self._process.pid if self._process is not None else None
        for pid in pids:
            if current_pid is not None and pid == current_pid:
                continue
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)

        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if not any(self._pid_exists(pid) for pid in pids if pid != current_pid):
                return
            time.sleep(0.25)

        for pid in pids:
            if current_pid is not None and pid == current_pid:
                continue
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, 0)
            return True
        return False


class AppiumSessionProvider:
    def __init__(self, config: AndroidAppConfig) -> None:
        self._config = config
        self._server = LocalAppiumServer(config)

    async def create_youtube_session(
        self,
        *,
        adb_serial: str,
        avd_name: str,
    ) -> AndroidDriverHandle:
        print(
            f"[android-appium] create_session:start avd={avd_name} serial={adb_serial}",
            flush=True,
        )
        started_local_server = await self._server.start()
        try:
            last_error = None
            for attempt in range(3):
                try:
                    print(
                        f"[android-appium] create_session:wait_serial attempt={attempt + 1} serial={adb_serial}",
                        flush=True,
                    )
                    try:
                        resolved_serial = await asyncio.wait_for(
                            anyio.to_thread.run_sync(self._wait_for_serial_sync, adb_serial),
                            timeout=60,
                        )
                    except asyncio.TimeoutError as exc:
                        raise AndroidAppiumError(
                            "Appium create_session:wait_serial timed out"
                        ) from exc
                    print(
                        f"[android-appium] create_session:wait_android_services attempt={attempt + 1} serial={resolved_serial}",
                        flush=True,
                    )
                    try:
                        await asyncio.wait_for(
                            anyio.to_thread.run_sync(
                                self._wait_for_android_services_sync,
                                resolved_serial,
                                not self._config.appium_skip_device_initialization,
                            ),
                            timeout=max(60, self._config.device_ready_timeout_seconds),
                        )
                    except asyncio.TimeoutError as exc:
                        raise AndroidAppiumError(
                            "Appium create_session:wait_android_services timed out"
                        ) from exc
                    print(
                        f"[android-appium] create_session:attempt={attempt + 1} serial={resolved_serial}",
                        flush=True,
                    )
                    print(
                        f"[android-appium] create_session:driver_create attempt={attempt + 1} serial={resolved_serial}",
                        flush=True,
                    )
                    skip_device_initialization = self._should_skip_device_initialization(
                        configured_skip_device_initialization=self._config.appium_skip_device_initialization,
                        attempt=attempt,
                    )
                    try:
                        driver = await asyncio.wait_for(
                            anyio.to_thread.run_sync(
                                self._create_driver_sync,
                                resolved_serial,
                                avd_name,
                                skip_device_initialization,
                            ),
                            timeout=self._config.appium_create_session_timeout_seconds,
                        )
                    except asyncio.TimeoutError as exc:
                        raise AndroidAppiumError(
                            "Appium create_session:driver_create timed out"
                        ) from exc
                    print(
                        f"[android-appium] create_session:driver_validate attempt={attempt + 1} serial={resolved_serial}",
                        flush=True,
                    )
                    try:
                        await asyncio.wait_for(
                            anyio.to_thread.run_sync(
                                self._validate_driver_sync,
                                driver,
                            ),
                            timeout=self._config.appium_validate_session_timeout_seconds,
                        )
                    except asyncio.TimeoutError as exc:
                        raise AndroidAppiumError(
                            "Appium create_session:driver_validate timed out"
                        ) from exc
                    await anyio.to_thread.run_sync(
                        self._set_runtime_command_timeout_sync,
                        driver,
                        self._config.appium_runtime_command_timeout_seconds,
                    )
                    print(
                        f"[android-appium] create_session:connected serial={resolved_serial}",
                        flush=True,
                    )
                    break
                except Exception as exc:
                    last_error = exc
                    print(
                        f"[android-appium] create_session:error attempt={attempt + 1} serial={adb_serial} error={type(exc).__name__}:{exc}",
                        flush=True,
                    )
                    with contextlib.suppress(Exception):
                        await anyio.to_thread.run_sync(self._quit_driver_sync, locals().get("driver"))
                    if self._is_sdk_env_error(exc) and self._config.manage_appium_server:
                        print(
                            "[android-appium] create_session:restart_server reason=sdk_env_missing",
                            flush=True,
                        )
                        started_local_server = await self._server.start(force_restart=True)
                        await asyncio.sleep(2)
                        continue
                    if not self._is_transient_device_error(exc) or attempt == 2:
                        raise
                    if started_local_server:
                        with contextlib.suppress(Exception):
                            await self._server.stop()
                        started_local_server = await self._server.start()
                    await anyio.to_thread.run_sync(self._reset_appium_helpers_sync, adb_serial)
                    if self._requires_adb_recovery(exc):
                        await anyio.to_thread.run_sync(self._recover_device_sync, adb_serial)
                    # Give UiAutomation / accessibility services extra time to settle.
                    # Retries run with full device initialization enabled.
                    await asyncio.sleep(10)
            else:
                raise last_error or AndroidAppiumError("Failed to create Appium session")
        except Exception:
            if started_local_server:
                await self._server.stop()
            raise
        return AndroidDriverHandle(
            driver=driver,
            server_url=self._config.appium_server_url,
            started_local_server=started_local_server,
        )

    async def close_session(self, handle: AndroidDriverHandle) -> None:
        print(
            f"[android-appium] close_session:start server={handle.server_url} local={str(handle.started_local_server).lower()}",
            flush=True,
        )
        try:
            await anyio.to_thread.run_sync(
                self._quit_driver_with_timeout_sync,
                handle.driver,
                self._config.appium_command_timeout_seconds,
            )
        except Exception:
            pass
        finally:
            if handle.started_local_server:
                await self._server.stop()
        print("[android-appium] close_session:done", flush=True)

    async def recover_session_environment(self, *, adb_serial: str) -> None:
        await anyio.to_thread.run_sync(self._reset_appium_helpers_sync, adb_serial)
        await anyio.to_thread.run_sync(self._recover_device_sync, adb_serial)

    async def close_broken_session(
        self,
        handle: AndroidDriverHandle,
        *,
        adb_serial: str,
    ) -> None:
        print(
            f"[android-appium] close_broken_session:start server={handle.server_url} local={str(handle.started_local_server).lower()}",
            flush=True,
        )
        try:
            await anyio.to_thread.run_sync(
                self._quit_driver_with_timeout_sync,
                handle.driver,
                self._config.appium_command_timeout_seconds,
            )
        except Exception:
            pass
        finally:
            if handle.started_local_server:
                await self._server.stop()
        with contextlib.suppress(Exception):
            await self.recover_session_environment(adb_serial=adb_serial)
        print("[android-appium] close_broken_session:done", flush=True)

    def tail_server_log(self, *, lines: int = 120) -> str:
        if lines <= 0:
            return ""
        log_path = self._server._log_path
        if not log_path.exists():
            return ""
        content = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return "\n".join(content[-lines:])

    def _create_driver_sync(
        self,
        adb_serial: str,
        avd_name: str,
        skip_device_initialization: bool,
    ) -> object:
        try:
            from appium import webdriver
            from appium.webdriver.appium_connection import AppiumConnection
            from appium.options.android import UiAutomator2Options
            from selenium.webdriver.remote.client_config import ClientConfig
        except ModuleNotFoundError as exc:
            raise AndroidAppiumError(
                "Appium Python client is missing. Install backend extra: [android]"
            ) from exc

        options = UiAutomator2Options()
        options.platform_name = "Android"
        options.automation_name = "UiAutomator2"
        options.device_name = avd_name
        options.udid = adb_serial
        options.app_package = self._config.youtube_package
        options.app_activity = self._config.youtube_activity
        options.no_reset = True
        options.new_command_timeout = max(
            180,
            self._config.appium_new_command_timeout_seconds,
        )
        options.auto_grant_permissions = True
        options.set_capability(
            "skipDeviceInitialization",
            skip_device_initialization,
        )
        options.set_capability("ignoreHiddenApiPolicyError", True)
        options.set_capability(
            "uiautomator2ServerLaunchTimeout",
            self._config.device_ready_timeout_seconds * 1000,
        )
        options.set_capability(
            "adbExecTimeout",
            self._config.adb_exec_timeout_seconds * 1000,
        )
        options.set_capability(
            "uiautomator2ServerInstallTimeout",
            self._config.uiautomator2_server_install_timeout_seconds * 1000,
        )
        options.set_capability(
            "androidInstallTimeout",
            self._config.uiautomator2_server_install_timeout_seconds * 1000,
        )

        try:
            client_config = ClientConfig(
                remote_server_addr=self._config.appium_server_url,
                timeout=self._config.appium_command_timeout_seconds,
                init_args_for_pool_manager=self._build_pool_manager_client_config(),
            )
            command_executor = AppiumConnection(
                remote_server_addr=self._config.appium_server_url,
                client_config=client_config,
            )
            return webdriver.Remote(command_executor=command_executor, options=options)
        except Exception as exc:
            raise AndroidAppiumError(f"Failed to create Appium session: {exc}") from exc

    @staticmethod
    def _build_pool_manager_client_config() -> dict[str, dict[str, int | bool]]:
        # Selenium/Appium reads pool overrides from a nested key on ClientConfig.
        # A flat {"maxsize": ...} dict is ignored by RemoteConnection._get_connection_manager().
        return {
            "init_args_for_pool_manager": {
                "maxsize": 8,
                "block": False,
            }
        }

    def _validate_driver_sync(self, driver: object) -> None:
        if driver is None:
            raise AndroidAppiumError("Appium driver was not created")
        last_error: Exception | None = None
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            try:
                session_id = getattr(driver, "session_id", None)
                if not session_id:
                    raise AndroidAppiumError("Appium session has no session_id yet")
                current_package = getattr(driver, "current_package", None)  # type: ignore[attr-defined]
                current_activity = getattr(driver, "current_activity", None)  # type: ignore[attr-defined]
                if not current_package and not current_activity:
                    raise AndroidAppiumError("Appium session returned empty package/activity")
                probe_elements = driver.find_elements("id", "android:id/content")  # type: ignore[attr-defined]
                if probe_elements is None:
                    raise AndroidAppiumError("Appium UiAutomator2 probe returned None")
                return
            except Exception as exc:
                last_error = exc
                time.sleep(1.0)
        raise AndroidAppiumError(
            f"Appium session health check failed: {last_error or 'unknown error'}"
        ) from last_error

    @staticmethod
    def _set_runtime_command_timeout_sync(driver: object, timeout_seconds: int) -> None:
        try:
            command_executor = getattr(driver, "command_executor", None)
            if command_executor is not None and hasattr(command_executor, "set_timeout"):
                command_executor.set_timeout(max(1, timeout_seconds))
        except Exception as exc:
            raise AndroidAppiumError(
                f"Failed to reduce Appium runtime command timeout: {exc}"
            ) from exc

    @staticmethod
    def _is_sdk_env_error(exc: Exception) -> bool:
        message = str(exc).casefold()
        return (
            "android_home" in message
            or "android_sdk_root" in message
            or "neither android_home nor android_sdk_root environment variable was exported" in message
        )

    @staticmethod
    def _is_transient_device_error(exc: Exception) -> bool:
        message = str(exc).casefold()
        return (
            "device offline" in message
            or "adbexec" in message
            or "get api level" in message
            or "adb serial did not become available" in message
            or "create_session:wait_serial" in message
            or "create_session:wait_android_services" in message
            or "create_session:driver_create" in message
            or "create_session:driver_validate" in message
            or "can't find service: settings" in message
            or "io.appium.settings" in message
            or "instrumentation process cannot be initialized" in message
            or "instrumentation process is not running" in message
            or "uiautomation not connected" in message
            or "sessionnotcreatedexception" in message
            or "socket hang up" in message
            or "read timed out" in message
            or "readtimeout" in message
            or "appium session health check failed" in message
            or "invalidsessionidexception" in message
            or "the session identified by" in message
            or "timed out" in message and "appium" in message
            or "appium server is not reachable" in message
            or "cannot start the 'io.appium.settings' application" in message
            or "activity class {io.appium.settings/io.appium.settings.settings} does not exist" in message
            or "127.0.0.1" in message and "4723" in message
        )

    @staticmethod
    def _requires_adb_recovery(exc: Exception) -> bool:
        message = str(exc).casefold()
        ui_automation_crash_markers = (
            "uiautomation not connected",
            "sessionnotcreatedexception",
            "instrumentation process cannot be initialized",
            "instrumentation process is not running",
            "socket hang up",
        )
        if any(marker in message for marker in ui_automation_crash_markers):
            return False
        return True

    @staticmethod
    def _should_skip_device_initialization(
        *,
        configured_skip_device_initialization: bool,
        attempt: int,
    ) -> bool:
        return configured_skip_device_initialization and attempt == 0

    @staticmethod
    def _device_ready_sync(
        adb_bin: str,
        env: dict[str, str],
        adb_serial: str,
    ) -> bool:
        state_result = subprocess.run(
            [adb_bin, "-s", adb_serial, "get-state"],
            check=False,
            capture_output=True,
            env=env,
            text=True,
            timeout=30,
        )
        if state_result.returncode != 0 or state_result.stdout.strip() != "device":
            return False
        ping_result = subprocess.run(
            [adb_bin, "-s", adb_serial, "shell", "echo", "ping"],
            check=False,
            capture_output=True,
            env=env,
            text=True,
            timeout=30,
        )
        return ping_result.returncode == 0 and "ping" in (ping_result.stdout or "")

    @classmethod
    def _recover_device_sync(cls, adb_serial: str) -> None:
        adb_bin = require_tool_path("adb")
        env = build_android_runtime_env()
        for _ in range(6):
            if AppiumSessionProvider._device_ready_sync(adb_bin, env, adb_serial):
                return
            time.sleep(2)
        subprocess.run(
            [adb_bin, "reconnect", "offline"],
            check=False,
            capture_output=True,
            env=env,
            text=True,
            timeout=30,
        )
        for _ in range(15):
            with contextlib.suppress(Exception):
                subprocess.run(
                    [adb_bin, "-s", adb_serial, "wait-for-device"],
                    check=False,
                    capture_output=True,
                    env=env,
                    text=True,
                    timeout=30,
                )
            if AppiumSessionProvider._device_ready_sync(adb_bin, env, adb_serial):
                return
            time.sleep(2)
        subprocess.run(
            [adb_bin, "reconnect", "device"],
            check=False,
            capture_output=True,
            env=env,
            text=True,
            timeout=30,
        )
        for _ in range(10):
            if AppiumSessionProvider._device_ready_sync(adb_bin, env, adb_serial):
                return
            time.sleep(2)

    @staticmethod
    def _reset_appium_helpers_sync(adb_serial: str) -> None:
        adb_bin = require_tool_path("adb")
        env = build_android_runtime_env()
        # Force-stop YouTube first so it cannot interfere with UiAutomator2
        # instrumentation re-initialization on the next Appium session attempt.
        subprocess.run(
            [adb_bin, "-s", adb_serial, "shell", "am", "force-stop",
             "com.google.android.youtube"],
            check=False,
            capture_output=True,
            env=env,
            text=True,
            timeout=15,
        )
        helper_packages = (
            "io.appium.settings",
            "io.appium.uiautomator2.server",
            "io.appium.uiautomator2.server.test",
        )
        for package_name in helper_packages:
            subprocess.run(
                [adb_bin, "-s", adb_serial, "shell", "am", "force-stop", package_name],
                check=False,
                capture_output=True,
                env=env,
                text=True,
                timeout=30,
            )
            subprocess.run(
                [adb_bin, "-s", adb_serial, "uninstall", package_name],
                check=False,
                capture_output=True,
                env=env,
                text=True,
                timeout=30,
            )

    @staticmethod
    def _wait_for_serial_sync(adb_serial: str) -> str:
        adb_bin = require_tool_path("adb")
        env = build_android_runtime_env()
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            state_result = subprocess.run(
                [adb_bin, "-s", adb_serial, "get-state"],
                check=False,
                capture_output=True,
                env=env,
                text=True,
                timeout=30,
            )
            if state_result.returncode == 0 and state_result.stdout.strip() == "device":
                return adb_serial
            result = subprocess.run(
                [adb_bin, "devices"],
                check=False,
                capture_output=True,
                env=env,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                emulator_devices: list[str] = []
                for line in result.stdout.splitlines():
                    if line.startswith(adb_serial) and "\tdevice" in line:
                        return adb_serial
                    if line.startswith("emulator-") and "\tdevice" in line:
                        serial, _, _ = line.partition("\t")
                        emulator_devices.append(serial)
                if len(emulator_devices) == 1:
                    return emulator_devices[0]
            time.sleep(2)
        raise AndroidAppiumError(f"ADB serial did not become available: {adb_serial}")

    @staticmethod
    def _wait_for_android_services_sync(
        adb_serial: str,
        require_settings_service: bool,
    ) -> None:
        adb_bin = require_tool_path("adb")
        env = build_android_runtime_env()
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if not require_settings_service:
                result = subprocess.run(
                    [adb_bin, "-s", adb_serial, "shell", "echo", "ping"],
                    check=False,
                    capture_output=True,
                    env=env,
                    text=True,
                    timeout=30,
                )
                if result.returncode == 0 and "ping" in (result.stdout or ""):
                    return
                time.sleep(2)
                continue

            result = subprocess.run(
                [
                    adb_bin,
                    "-s",
                    adb_serial,
                    "shell",
                    "settings",
                    "get",
                    "global",
                    "adb_enabled",
                ],
                check=False,
                capture_output=True,
                env=env,
                text=True,
                timeout=30,
            )
            if (
                result.returncode == 0
                and "can't find service: settings" not in (result.stderr or "").casefold()
                and "can't find service: settings" not in (result.stdout or "").casefold()
            ):
                return
            time.sleep(2)
        raise AndroidAppiumError(
            f"Android services did not become ready for Appium session: {adb_serial}"
        )

    @staticmethod
    def _quit_driver_sync(driver: object) -> None:
        try:
            driver.quit()  # type: ignore[attr-defined]
        except Exception:
            pass

    @classmethod
    def _quit_driver_with_timeout_sync(cls, driver: object, timeout_seconds: int) -> None:
        worker = threading.Thread(
            target=cls._quit_driver_sync,
            args=(driver,),
            daemon=True,
        )
        worker.start()
        worker.join(timeout=max(1, timeout_seconds))
