from __future__ import annotations

import asyncio
import contextlib
import signal
import time
from dataclasses import dataclass
from pathlib import Path

from .tooling import build_android_runtime_env, require_tool_path


@dataclass(frozen=True)
class AndroidScreenRecordingHandle:
    adb_serial: str
    remote_path: str
    local_path: Path
    process: asyncio.subprocess.Process
    started_monotonic: float


class AndroidScreenRecorder:
    def __init__(self, *, adb_serial: str, artifacts_dir: Path, bitrate: int) -> None:
        self._adb_serial = adb_serial
        self._artifacts_dir = artifacts_dir
        self._bitrate = bitrate

    async def start(self, *, artifact_prefix: str) -> AndroidScreenRecordingHandle:
        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        remote_path = f"/sdcard/Download/{artifact_prefix}.mp4"
        local_path = self._artifacts_dir / f"{artifact_prefix}.mp4"
        adb_bin = require_tool_path("adb")
        process = await asyncio.create_subprocess_exec(
            adb_bin,
            "-s",
            self._adb_serial,
            "shell",
            "screenrecord",
            "--bit-rate",
            str(self._bitrate),
            remote_path,
            env=build_android_runtime_env(),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        started_monotonic = time.monotonic()
        await asyncio.sleep(1.0)
        return AndroidScreenRecordingHandle(
            adb_serial=self._adb_serial,
            remote_path=remote_path,
            local_path=local_path,
            process=process,
            started_monotonic=started_monotonic,
        )

    async def stop(
        self,
        handle: AndroidScreenRecordingHandle,
        *,
        keep_local: bool,
    ) -> Path | None:
        adb_bin = require_tool_path("adb")
        with contextlib.suppress(Exception):
            signal_remote = await asyncio.create_subprocess_exec(
                adb_bin,
                "-s",
                handle.adb_serial,
                "shell",
                "pkill",
                "-INT",
                "screenrecord",
                env=build_android_runtime_env(),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await signal_remote.communicate()
            await asyncio.sleep(2.0)

        with contextlib.suppress(ProcessLookupError):
            handle.process.send_signal(signal.SIGINT)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(handle.process.wait(), timeout=10)
        if handle.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                handle.process.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(handle.process.wait(), timeout=10)
        if keep_local:
            for _pull_attempt in range(3):
                pull = await asyncio.create_subprocess_exec(
                    adb_bin,
                    "-s",
                    handle.adb_serial,
                    "pull",
                    handle.remote_path,
                    str(handle.local_path),
                    env=build_android_runtime_env(),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await pull.communicate()
                if handle.local_path.exists() and handle.local_path.stat().st_size > 0:
                    break
                # Pull produced empty/no file — wait briefly and retry
                if _pull_attempt < 2:
                    await asyncio.sleep(2.0)
        cleanup = await asyncio.create_subprocess_exec(
            adb_bin,
            "-s",
            handle.adb_serial,
            "shell",
            "rm",
            "-f",
            handle.remote_path,
            env=build_android_runtime_env(),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await cleanup.communicate()

        if keep_local and handle.local_path.exists() and handle.local_path.stat().st_size > 0:
            return handle.local_path
        if handle.local_path.exists():
            handle.local_path.unlink(missing_ok=True)
        return None
