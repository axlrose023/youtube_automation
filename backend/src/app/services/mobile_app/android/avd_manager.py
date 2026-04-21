from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from asyncio.subprocess import DEVNULL
from dataclasses import dataclass
from pathlib import Path

from .errors import AndroidDeviceStartError, AndroidToolingError
from .tooling import build_android_runtime_env, resolve_tool_path


@dataclass(frozen=True)
class AndroidDeviceHandle:
    avd_name: str
    adb_serial: str
    reused_running_device: bool


@dataclass(frozen=True)
class AndroidAvdMetadata:
    avd_name: str
    ini_path: Path
    avd_dir: Path
    exists: bool
    play_store_enabled: bool
    system_image_dir: str | None = None
    tag_id: str | None = None
    tag_display: str | None = None
    device_name: str | None = None
    target: str | None = None


@dataclass(frozen=True)
class AndroidEmulatorLaunchOptions:
    headless: bool
    gpu_mode: str
    accel_mode: str | None = None
    http_proxy: str | None = None
    load_snapshot: bool = False
    save_snapshot: bool = False
    snapshot_name: str | None = None
    force_snapshot_load: bool = False
    skip_adb_auth: bool = True
    force_stop_running: bool = False


class AndroidAvdManager:
    def __init__(
        self,
        *,
        emulator_start_timeout_seconds: int,
        device_ready_timeout_seconds: int,
    ) -> None:
        self._emulator_start_timeout_seconds = emulator_start_timeout_seconds
        self._device_ready_timeout_seconds = device_ready_timeout_seconds

    async def ensure_device(
        self,
        *,
        avd_name: str,
        launch: AndroidEmulatorLaunchOptions,
    ) -> AndroidDeviceHandle:
        self._ensure_avd_runtime_properties(avd_name)
        print(f"[android-avd] ensure_device:start avd={avd_name}", flush=True)
        adb_bin = await self._ensure_tool("adb")
        emulator_bin = await self._ensure_tool("emulator")

        print(f"[android-avd] ensure_device:find_running avd={avd_name}", flush=True)
        existing_serial = await self._find_running_avd_serial(adb_bin, avd_name)
        if existing_serial:
            if launch.force_stop_running:
                print(
                    f"[android-avd] ensure_device:stop_running avd={avd_name} serial={existing_serial}",
                    flush=True,
                )
                await self.stop_device(existing_serial, avd_name=avd_name)
                await self._cleanup_stale_avd_state(avd_name)
            else:
                try:
                    print(
                        f"[android-avd] ensure_device:reuse_running avd={avd_name} serial={existing_serial}",
                        flush=True,
                    )
                    await self._wait_for_boot_completed(adb_bin, existing_serial)
                    await self._stabilize_device(adb_bin, existing_serial)
                    await self._unlock_device(adb_bin, existing_serial)
                    print(
                        f"[android-avd] ensure_device:reused_ready avd={avd_name} serial={existing_serial}",
                        flush=True,
                    )
                    return AndroidDeviceHandle(
                        avd_name=avd_name,
                        adb_serial=existing_serial,
                        reused_running_device=True,
                    )
                except AndroidDeviceStartError:
                    with contextlib.suppress(Exception):
                        await self.stop_device(existing_serial, avd_name=avd_name)
                    await self._cleanup_stale_avd_state(avd_name)

        print(f"[android-avd] ensure_device:list_existing_serials avd={avd_name}", flush=True)
        existing_serials = set(await self._list_all_emulator_serials(adb_bin))
        print(f"[android-avd] ensure_device:cleanup_stale avd={avd_name}", flush=True)
        await self._cleanup_stale_avd_state(avd_name)
        print(f"[android-avd] ensure_device:start_emulator avd={avd_name}", flush=True)
        process = await self._start_emulator_process(
            emulator_bin=emulator_bin,
            avd_name=avd_name,
            launch=launch,
        )
        try:
            print(f"[android-avd] ensure_device:wait_new_serial avd={avd_name}", flush=True)
            new_serial = await self._wait_for_new_serial(
                adb_bin,
                existing_serials,
                process=process,
                avd_name=avd_name,
            )
            print(
                f"[android-avd] ensure_device:wait_boot avd={avd_name} serial={new_serial}",
                flush=True,
            )
            await self._wait_for_boot_completed(adb_bin, new_serial)
            print(
                f"[android-avd] ensure_device:stabilize avd={avd_name} serial={new_serial}",
                flush=True,
            )
            await self._stabilize_device(adb_bin, new_serial)
            print(
                f"[android-avd] ensure_device:unlock avd={avd_name} serial={new_serial}",
                flush=True,
            )
            await self._unlock_device(adb_bin, new_serial)
            # Extra warm-up on cold boot: UiAutomation / accessibility services need
            # additional time to fully initialize before Appium connects.
            print(
                f"[android-avd] ensure_device:cold_boot_warmup avd={avd_name} serial={new_serial}",
                flush=True,
            )
            await asyncio.sleep(10)
            print(
                f"[android-avd] ensure_device:ready avd={avd_name} serial={new_serial}",
                flush=True,
            )
            return AndroidDeviceHandle(
                avd_name=avd_name,
                adb_serial=new_serial,
                reused_running_device=False,
            )
        except Exception:
            with contextlib.suppress(Exception):
                process.terminate()
            raise

    async def stop_device(self, adb_serial: str, *, avd_name: str | None = None) -> None:
        adb_bin = await self._ensure_tool("adb")
        await self._run(adb_bin, "-s", adb_serial, "emu", "kill", check=False)
        if await self._wait_for_serial_removed(adb_bin, adb_serial, timeout_seconds=12):
            await self._cleanup_orphaned_emulator_crashpad_handlers()
            return

        resolved_avd_name = avd_name or await self._resolve_avd_name(adb_bin, adb_serial)
        if resolved_avd_name:
            await self._force_stop_qemu_processes(resolved_avd_name, signal.SIGTERM)
            if await self._wait_for_serial_removed(adb_bin, adb_serial, timeout_seconds=8):
                await self._cleanup_orphaned_emulator_crashpad_handlers()
                return
            await self._force_stop_qemu_processes(resolved_avd_name, signal.SIGKILL)
            if await self._wait_for_serial_removed(adb_bin, adb_serial, timeout_seconds=5):
                await self._cleanup_orphaned_emulator_crashpad_handlers()
                return

        raise AndroidDeviceStartError(f"Timed out stopping emulator device: {adb_serial}")

    async def force_cleanup_device(
        self,
        *,
        adb_serial: str | None = None,
        avd_name: str | None = None,
    ) -> None:
        adb_bin = await self._ensure_tool("adb")
        resolved_avd_name = avd_name
        if resolved_avd_name is None and adb_serial is not None:
            with contextlib.suppress(Exception):
                resolved_avd_name = await self._resolve_avd_name(adb_bin, adb_serial)
        if adb_serial is not None:
            with contextlib.suppress(Exception):
                await self._run(adb_bin, "-s", adb_serial, "emu", "kill", check=False)
        if resolved_avd_name:
            await self._force_stop_qemu_processes(resolved_avd_name, signal.SIGTERM)
            await asyncio.sleep(2)
            await self._force_stop_qemu_processes(resolved_avd_name, signal.SIGKILL)
            await asyncio.sleep(1)
            await self._cleanup_stale_avd_state(resolved_avd_name)
        await self._cleanup_orphaned_emulator_crashpad_handlers()

    async def _wait_for_serial_removed(
        self,
        adb_bin: str,
        adb_serial: str,
        *,
        timeout_seconds: int = 30,
    ) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            current_serials = await self._list_all_emulator_serials(adb_bin)
            if adb_serial not in current_serials:
                return True
            await asyncio.sleep(1)
        return False

    async def get_avd_metadata(self, avd_name: str) -> AndroidAvdMetadata:
        ini_path = Path.home() / ".android" / "avd" / f"{avd_name}.ini"
        avd_dir = Path.home() / ".android" / "avd" / f"{avd_name}.avd"
        if not ini_path.exists() or not avd_dir.exists():
            return AndroidAvdMetadata(
                avd_name=avd_name,
                ini_path=ini_path,
                avd_dir=avd_dir,
                exists=False,
            )

        ini_props = self._read_properties_file(ini_path)
        config_props = self._read_properties_file(avd_dir / "config.ini")
        tag_id = config_props.get("tag.id")
        system_image_dir = config_props.get("image.sysdir.1")
        play_store_enabled = (
            config_props.get("PlayStore.enabled", "").lower() == "yes"
            or tag_id == "google_apis_playstore"
            or (
                system_image_dir is not None
                and "google_apis_playstore" in system_image_dir
            )
        )
        return AndroidAvdMetadata(
            avd_name=avd_name,
            ini_path=ini_path,
            avd_dir=avd_dir,
            exists=True,
            play_store_enabled=play_store_enabled,
            system_image_dir=system_image_dir,
            tag_id=tag_id,
            tag_display=config_props.get("tag.display"),
            device_name=config_props.get("hw.device.name"),
            target=ini_props.get("target"),
        )

    async def ensure_avd(
        self,
        *,
        avd_name: str,
        system_image_package: str,
        device_preset: str,
    ) -> bool:
        if (Path.home() / ".android" / "avd" / f"{avd_name}.avd").exists():
            return False

        sdkmanager_bin = await self._ensure_tool("sdkmanager")
        avdmanager_bin = await self._ensure_tool("avdmanager")
        await self._run(sdkmanager_bin, system_image_package)
        await self._run(
            avdmanager_bin,
            "create",
            "avd",
            "--force",
            "--name",
            avd_name,
            "--package",
            system_image_package,
            "--device",
            device_preset,
            input_text="no\n",
        )
        self._ensure_avd_runtime_properties(avd_name)
        return True

    async def save_snapshot(self, adb_serial: str, snapshot_name: str) -> None:
        adb_bin = await self._ensure_tool("adb")
        await self._run(
            adb_bin,
            "-s",
            adb_serial,
            "emu",
            "avd",
            "snapshot",
            "save",
            snapshot_name,
        )

    async def list_snapshots(self, adb_serial: str) -> list[str]:
        adb_bin = await self._ensure_tool("adb")
        result = await self._run(
            adb_bin,
            "-s",
            adb_serial,
            "emu",
            "avd",
            "snapshot",
            "list",
            check=False,
        )
        snapshots: list[str] = []
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("OK") or line.startswith("List of snapshots"):
                continue
            name = line.split()[0]
            if name and name not in {"There", "No"}:
                snapshots.append(name)
        return snapshots

    async def snapshot_exists(self, avd_name: str, snapshot_name: str) -> bool:
        snapshot_dir = (
            Path.home()
            / ".android"
            / "avd"
            / f"{avd_name}.avd"
            / "snapshots"
            / snapshot_name
        )
        snapshot_pb = snapshot_dir / "snapshot.pb"
        ram_bin = snapshot_dir / "ram.bin"
        return snapshot_pb.exists() and ram_bin.exists()

    async def _ensure_tool(self, tool_name: str) -> str:
        resolved = resolve_tool_path(tool_name)
        if resolved:
            return resolved
        raise AndroidToolingError(f"Required tool is missing: {tool_name}")

    async def _start_emulator_process(
        self,
        *,
        emulator_bin: str,
        avd_name: str,
        launch: AndroidEmulatorLaunchOptions,
    ) -> asyncio.subprocess.Process:
        args = [
            emulator_bin,
            f"@{avd_name}",
            "-netdelay",
            "none",
            "-netspeed",
            "full",
            "-gpu",
            (
                "swiftshader_indirect"
                if (not launch.headless and launch.gpu_mode == "host")
                else launch.gpu_mode
            ),
            "-no-boot-anim",
            "-camera-back",
            "none",
            "-camera-front",
            "none",
        ]
        if launch.skip_adb_auth:
            args.append("-skip-adb-auth")
        if launch.accel_mode:
            args.extend(["-accel", launch.accel_mode])
        if launch.http_proxy:
            args.extend(["-http-proxy", launch.http_proxy])
        if launch.snapshot_name:
            args.extend(["-snapshot", launch.snapshot_name])
            if launch.force_snapshot_load:
                args.append("-force-snapshot-load")
        elif not launch.load_snapshot:
            args.append("-no-snapshot-load")
        if not launch.save_snapshot:
            args.append("-no-snapshot-save")
        if launch.headless:
            args.extend(["-no-window", "-no-audio"])

        return await asyncio.create_subprocess_exec(
            *args,
            env=build_android_runtime_env(),
            stdout=DEVNULL,
            stderr=DEVNULL,
        )

    async def _find_running_avd_serial(self, adb_bin: str, avd_name: str) -> str | None:
        for serial in await self._list_emulator_serials(adb_bin):
            result = await self._run(
                adb_bin,
                "-s",
                serial,
                "emu",
                "avd",
                "name",
                check=False,
            )
            resolved_name = self._parse_emulator_avd_name(result.stdout)
            if result.returncode == 0 and resolved_name == avd_name:
                return serial
        return None

    async def _list_emulator_serials(self, adb_bin: str) -> list[str]:
        result = await self._run(adb_bin, "devices")
        serials: list[str] = []
        for line in result.stdout.splitlines():
            if "\tdevice" not in line:
                continue
            serial, _, state = line.partition("\t")
            if state.strip() == "device" and serial.startswith("emulator-"):
                serials.append(serial)
        return serials

    async def _list_all_emulator_serials(self, adb_bin: str) -> list[str]:
        result = await self._run(adb_bin, "devices", check=False)
        serials: list[str] = []
        for line in result.stdout.splitlines():
            if "\t" not in line:
                continue
            serial, _, _state = line.partition("\t")
            if serial.startswith("emulator-"):
                serials.append(serial)
        return serials

    async def _wait_for_new_serial(
        self,
        adb_bin: str,
        existing_serials: set[str],
        *,
        process: asyncio.subprocess.Process | None = None,
        avd_name: str | None = None,
    ) -> str:
        deadline = time.monotonic() + self._emulator_start_timeout_seconds
        while time.monotonic() < deadline:
            if process is not None and process.returncode is not None:
                if not avd_name or not await self._find_stale_avd_pids(avd_name):
                    raise AndroidDeviceStartError(
                        "Emulator process exited before adb device appeared"
                        + (f" (avd={avd_name})" if avd_name else "")
                    )
            current_serials = set(await self._list_all_emulator_serials(adb_bin))
            new_serials = current_serials - existing_serials
            if new_serials:
                print(
                    "[android-avd] wait_new_serial:new_serials "
                    f"avd={avd_name or 'unknown'} existing={sorted(existing_serials)} "
                    f"current={sorted(current_serials)} selected={sorted(new_serials)[0]}",
                    flush=True,
                )
                return sorted(new_serials)[0]
            if avd_name:
                reused_serial = await self._find_current_serial_for_avd_name(
                    adb_bin,
                    avd_name,
                    current_serials,
                )
                if reused_serial:
                    print(
                        "[android-avd] wait_new_serial:reused_serial "
                        f"avd={avd_name} existing={sorted(existing_serials)} "
                        f"current={sorted(current_serials)} selected={reused_serial}",
                        flush=True,
                    )
                    return reused_serial
            await asyncio.sleep(2)
        raise AndroidDeviceStartError("Timed out waiting for emulator device to appear in adb")

    async def _find_current_serial_for_avd_name(
        self,
        adb_bin: str,
        avd_name: str,
        current_serials: set[str],
    ) -> str | None:
        for serial in sorted(current_serials):
            result = await self._run(
                adb_bin,
                "-s",
                serial,
                "emu",
                "avd",
                "name",
                check=False,
                timeout_seconds=5,
            )
            resolved_name = self._parse_emulator_avd_name(result.stdout)
            if result.returncode == 0 and resolved_name == avd_name:
                return serial
        return None

    async def _wait_for_boot_completed(self, adb_bin: str, adb_serial: str) -> None:
        deadline = time.monotonic() + self._device_ready_timeout_seconds
        while time.monotonic() < deadline:
            state_result = await self._run(
                adb_bin,
                "-s",
                adb_serial,
                "get-state",
                check=False,
            )
            if state_result.returncode != 0 or state_result.stdout.strip() != "device":
                await asyncio.sleep(2)
                continue
            result = await self._run(
                adb_bin,
                "-s",
                adb_serial,
                "shell",
                "getprop",
                "sys.boot_completed",
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip() == "1":
                return
            await asyncio.sleep(2)
        raise AndroidDeviceStartError(
            f"Timed out waiting for Android device boot completion: {adb_serial}"
        )

    async def _stabilize_device(self, adb_bin: str, adb_serial: str) -> None:
        consecutive_ready = 0
        for _ in range(12):
            state_result = await self._run(
                adb_bin,
                "-s",
                adb_serial,
                "get-state",
                check=False,
            )
            ping_result = await self._run(
                adb_bin,
                "-s",
                adb_serial,
                "shell",
                "echo",
                "ping",
                check=False,
            )
            sdk_result = await self._run(
                adb_bin,
                "-s",
                adb_serial,
                "shell",
                "getprop",
                "ro.build.version.sdk",
                check=False,
            )
            if (
                state_result.returncode == 0
                and state_result.stdout.strip() == "device"
                and ping_result.returncode == 0
                and "ping" in ping_result.stdout
                and sdk_result.returncode == 0
                and sdk_result.stdout.strip().isdigit()
            ):
                consecutive_ready += 1
                if consecutive_ready >= 2:
                    await self._apply_runtime_tuning(adb_bin, adb_serial)
                    await asyncio.sleep(2)
                    return
            else:
                consecutive_ready = 0
            await asyncio.sleep(2)

        raise AndroidDeviceStartError(
            f"Android device did not stabilize for Appium session: {adb_serial}"
        )

    async def _unlock_device(self, adb_bin: str, adb_serial: str) -> None:
        await self._run(
            adb_bin,
            "-s",
            adb_serial,
            "shell",
            "input",
            "keyevent",
            "82",
            check=False,
        )

    async def _apply_runtime_tuning(self, adb_bin: str, adb_serial: str) -> None:
        tuning_commands: tuple[tuple[str, ...], ...] = (
            (
                adb_bin,
                "-s",
                adb_serial,
                "shell",
                "settings",
                "put",
                "global",
                "window_animation_scale",
                "0",
            ),
            (
                adb_bin,
                "-s",
                adb_serial,
                "shell",
                "settings",
                "put",
                "global",
                "transition_animation_scale",
                "0",
            ),
            (
                adb_bin,
                "-s",
                adb_serial,
                "shell",
                "settings",
                "put",
                "global",
                "animator_duration_scale",
                "0",
            ),
            (
                adb_bin,
                "-s",
                adb_serial,
                "shell",
                "cmd",
                "overlay",
                "enable-exclusive",
                "--category",
                "com.android.internal.systemui.navbar.threebutton",
            ),
        )
        for command in tuning_commands:
            await self._run(*command, check=False, timeout_seconds=30)
        await asyncio.sleep(1)

    async def _resolve_avd_name(self, adb_bin: str, adb_serial: str) -> str | None:
        result = await self._run(
            adb_bin,
            "-s",
            adb_serial,
            "emu",
            "avd",
            "name",
            check=False,
        )
        if result.returncode != 0:
            return None
        name = self._parse_emulator_avd_name(result.stdout) or ""
        return name or None

    @staticmethod
    def _parse_emulator_avd_name(stdout: str) -> str | None:
        for raw_line in stdout.splitlines():
            line = raw_line.strip()
            if not line or line == "OK":
                continue
            return line
        return None

    async def _cleanup_stale_avd_state(self, avd_name: str) -> None:
        stale_pids = await self._find_stale_avd_pids(avd_name)
        if stale_pids:
            for pid in stale_pids:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGTERM)
            await asyncio.sleep(2)
            remaining_pids = await self._find_stale_avd_pids(avd_name)
            for pid in remaining_pids:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
            await asyncio.sleep(1)

        avd_root = Path.home() / ".android" / "avd" / f"{avd_name}.avd"
        for lock_name in ("hardware-qemu.ini.lock", "multiinstance.lock"):
            lock_path = avd_root / lock_name
            if lock_path.exists():
                lock_path.unlink(missing_ok=True)

    async def _cleanup_orphaned_emulator_crashpad_handlers(self) -> None:
        if await self._find_any_running_emulator_pids():
            return
        for pid in await self._find_orphaned_emulator_crashpad_pids():
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)
        await asyncio.sleep(1)
        if await self._find_any_running_emulator_pids():
            return
        for pid in await self._find_orphaned_emulator_crashpad_pids():
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)

    async def _find_any_running_emulator_pids(self) -> list[int]:
        result = await self._run("ps", "ax", "-o", "pid=,command=", check=False)
        pids: list[int] = []
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if "qemu-system-aarch64" not in line and "/emulator" not in line:
                continue
            if "crashpad_handler" in line:
                continue
            pid_text, _, _ = line.partition(" ")
            try:
                pids.append(int(pid_text))
            except ValueError:
                continue
        return pids

    async def _find_orphaned_emulator_crashpad_pids(self) -> list[int]:
        result = await self._run("ps", "ax", "-o", "pid=,ppid=,command=", check=False)
        pids: list[int] = []
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if "crashpad_handler" not in line or "AndroidEmulator" not in line:
                continue
            parts = line.split(maxsplit=2)
            if len(parts) != 3:
                continue
            pid_text, ppid_text, _command = parts
            try:
                pid = int(pid_text)
                ppid = int(ppid_text)
            except ValueError:
                continue
            if ppid != 1:
                continue
            pids.append(pid)
        return pids

    async def _find_stale_avd_pids(self, avd_name: str) -> list[int]:
        result = await self._run("ps", "ax", "-o", "pid=,command=", check=False)
        pids: list[int] = []
        avd_token = f"@{avd_name}"
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if avd_token not in line:
                continue
            if "qemu-system-aarch64" not in line and "/emulator" not in line:
                continue
            pid_text, _, _ = line.partition(" ")
            try:
                pids.append(int(pid_text))
            except ValueError:
                continue
        return pids

    async def _force_stop_qemu_processes(
        self,
        avd_name: str,
        kill_signal: signal.Signals,
    ) -> None:
        for pid in await self._find_stale_avd_pids(avd_name):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, kill_signal)

    @staticmethod
    def _read_properties_file(path: Path) -> dict[str, str]:
        props: dict[str, str] = {}
        if not path.exists():
            return props
        for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            props[key.strip()] = value.strip()
        return props

    def _ensure_avd_runtime_properties(self, avd_name: str) -> None:
        config_path = Path.home() / ".android" / "avd" / f"{avd_name}.avd" / "config.ini"
        self._upsert_properties_file(config_path, {"hw.keyboard": "yes"})

    @staticmethod
    def _upsert_properties_file(path: Path, updates: dict[str, str]) -> None:
        if not path.exists():
            return

        existing_lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        pending = dict(updates)
        updated_lines: list[str] = []

        for line in existing_lines:
            if "=" not in line:
                updated_lines.append(line)
                continue

            key, _value = line.split("=", 1)
            stripped_key = key.strip()
            replacement = pending.pop(stripped_key, None)
            if replacement is None:
                updated_lines.append(line)
                continue
            updated_lines.append(f"{stripped_key}={replacement}")

        for key, value in pending.items():
            updated_lines.append(f"{key}={value}")

        path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")

    async def _run(
        self,
        *args: str,
        check: bool = True,
        input_text: str | None = None,
        timeout_seconds: float = 90.0,
    ) -> asyncio.subprocess.Process:
        process = await asyncio.create_subprocess_exec(
            *args,
            env=build_android_runtime_env(),
            stdin=asyncio.subprocess.PIPE if input_text is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(
                    input_text.encode("utf-8") if input_text is not None else None
                ),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            with contextlib.suppress(Exception):
                await process.communicate()
            raise AndroidToolingError(
                f"Command timed out after {timeout_seconds:.0f}s: {' '.join(args)}"
            ) from exc
        result = type(
            "CompletedProcess",
            (),
            {
                "returncode": process.returncode,
                "stdout": stdout.decode("utf-8", errors="ignore"),
                "stderr": stderr.decode("utf-8", errors="ignore"),
            },
        )()
        if check and result.returncode != 0:
            command = " ".join(args)
            raise AndroidToolingError(
                f"Command failed ({command}): {result.stderr.strip() or result.stdout.strip()}"
            )
        return result
