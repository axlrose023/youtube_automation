from __future__ import annotations

import json
import shutil
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import anyio

from app.services.mobile_app.models import AndroidWarmSnapshotBootstrapResult
from app.settings import Config

from .avd_manager import AndroidAvdMetadata, AndroidEmulatorLaunchOptions
from .errors import AndroidToolingError
from .runtime import build_android_probe_runtime
from .tooling import build_android_runtime_env, require_tool_path
from .youtube.navigator import AndroidYouTubeNavigator


class AndroidWarmSnapshotBootstrapper:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._runtime = build_android_probe_runtime(config.android_app)

    async def prepare(
        self,
        *,
        avd_name: str | None = None,
        system_image_package: str | None = None,
        device_preset: str | None = None,
    ) -> AndroidWarmSnapshotBootstrapResult:
        resolved_avd_name = avd_name or self._config.android_app.bootstrap_avd_name
        resolved_system_image = (
            system_image_package or self._config.android_app.bootstrap_system_image_package
        )
        resolved_device_preset = (
            device_preset or self._config.android_app.bootstrap_device_preset
        )

        created_avd = await self._runtime.avd_manager.ensure_avd(
            avd_name=resolved_avd_name,
            system_image_package=resolved_system_image,
            device_preset=resolved_device_preset,
        )
        metadata = await self._runtime.avd_manager.get_avd_metadata(resolved_avd_name)
        self._assert_play_store_bootstrap_target(
            metadata=metadata,
            expected_system_image_package=resolved_system_image,
        )

        device = await self._runtime.avd_manager.ensure_device(
            avd_name=resolved_avd_name,
            launch=AndroidEmulatorLaunchOptions(
                headless=False,
                gpu_mode=self._config.android_app.bootstrap_emulator_gpu_mode,
                accel_mode=self._config.android_app.bootstrap_emulator_accel_mode,
                load_snapshot=False,
                save_snapshot=False,
                skip_adb_auth=self._config.android_app.emulator_skip_adb_auth,
            ),
        )
        play_store_available = await anyio.to_thread.run_sync(
            self._has_package_sync,
            device.adb_serial,
            "com.android.vending",
        )
        if not play_store_available:
            raise AndroidToolingError(
                "Bootstrap AVD does not contain Play Store. "
                "Use a google_apis_playstore system image."
            )

        opened_play_store = await anyio.to_thread.run_sync(
            self._open_play_store_youtube_page_sync,
            device.adb_serial,
        )
        notes: list[str] = []
        if not opened_play_store:
            notes.append("play_store_launch_failed")

        prepare_artifacts = await anyio.to_thread.run_sync(
            self._capture_prepare_artifacts_sync,
            device.adb_serial,
        )
        notes.extend(prepare_artifacts["notes"])

        artifact_path = self._write_artifact(
            avd_name=device.avd_name,
            adb_serial=device.adb_serial,
            snapshot_name=self._config.android_app.warm_snapshot_name,
            created_avd=created_avd,
            play_store_available=play_store_available,
            opened_play_store=opened_play_store,
            notes=notes,
            phase="prepare",
            metadata=metadata,
            extra=prepare_artifacts["extra"],
        )
        return AndroidWarmSnapshotBootstrapResult(
            avd_name=device.avd_name,
            adb_serial=device.adb_serial,
            snapshot_name=self._config.android_app.warm_snapshot_name,
            created_avd=created_avd,
            play_store_available=play_store_available,
            opened_play_store=opened_play_store,
            artifact_path=artifact_path,
            notes=notes,
        )

    async def finalize(
        self,
        *,
        avd_name: str | None = None,
        snapshot_name: str | None = None,
        stop_after_save: bool = True,
    ) -> AndroidWarmSnapshotBootstrapResult:
        resolved_avd_name = avd_name or self._config.android_app.bootstrap_avd_name
        resolved_snapshot_name = (
            snapshot_name or self._config.android_app.warm_snapshot_name
        )
        metadata = await self._runtime.avd_manager.get_avd_metadata(resolved_avd_name)
        self._assert_play_store_bootstrap_target(metadata=metadata)

        device = None
        session = None
        notes: list[str] = []
        extra: dict[str, object] = {}
        try:
            device = await self._runtime.avd_manager.ensure_device(
                avd_name=resolved_avd_name,
                launch=AndroidEmulatorLaunchOptions(
                    headless=False,
                    gpu_mode=self._config.android_app.bootstrap_emulator_gpu_mode,
                    accel_mode=self._config.android_app.bootstrap_emulator_accel_mode,
                    load_snapshot=False,
                    save_snapshot=False,
                    skip_adb_auth=self._config.android_app.emulator_skip_adb_auth,
                ),
            )
            session = await self._runtime.appium_provider.create_youtube_session(
                adb_serial=device.adb_serial,
                avd_name=device.avd_name,
            )
            navigator = AndroidYouTubeNavigator(
                session.driver,
                self._config.android_app,
                adb_serial=device.adb_serial,
            )
            await navigator.ensure_app_ready()
            package, activity, _ = await navigator.describe_surface()
            notes.append(f"validated:{package or '<none>'}:{activity or '<none>'}")
            extra.update(
                await anyio.to_thread.run_sync(
                    self._capture_finalize_artifacts_sync,
                    session.driver,
                    "ready",
                )
            )
        except Exception as exc:
            extra.update(
                await anyio.to_thread.run_sync(
                    self._capture_finalize_artifacts_sync,
                    session.driver,
                    "failed",
                )
            )
            notes.append(f"validation_failed:{type(exc).__name__}")
            raise
        finally:
            if session is not None:
                await self._runtime.appium_provider.close_session(session)

        if device is None:
            raise AndroidToolingError("Bootstrap finalize did not initialize an Android device")
        await self._runtime.avd_manager.save_snapshot(
            device.adb_serial,
            resolved_snapshot_name,
        )
        available_snapshots = await self._runtime.avd_manager.list_snapshots(device.adb_serial)
        snapshot_exists_on_disk = await self._runtime.avd_manager.snapshot_exists(
            device.avd_name,
            resolved_snapshot_name,
        )
        if resolved_snapshot_name not in available_snapshots and not snapshot_exists_on_disk:
            raise AndroidToolingError(
                f"Snapshot save was requested but '{resolved_snapshot_name}' was not confirmed "
                "by emulator CLI or filesystem"
            )
        extra["available_snapshots"] = available_snapshots
        extra["snapshot_exists_on_disk"] = snapshot_exists_on_disk
        if snapshot_exists_on_disk and resolved_snapshot_name not in available_snapshots:
            notes.append("snapshot_validated_via_filesystem")

        if stop_after_save:
            await self._runtime.avd_manager.stop_device(
                device.adb_serial,
                avd_name=device.avd_name,
            )
            notes.append("device_stopped")

        artifact_path = self._write_artifact(
            avd_name=device.avd_name,
            adb_serial=device.adb_serial,
            snapshot_name=resolved_snapshot_name,
            created_avd=False,
            play_store_available=True,
            opened_play_store=True,
            notes=notes,
            phase="finalize",
            metadata=metadata,
            extra=extra,
        )
        return AndroidWarmSnapshotBootstrapResult(
            avd_name=device.avd_name,
            adb_serial=device.adb_serial,
            snapshot_name=resolved_snapshot_name,
            created_avd=False,
            play_store_available=True,
            opened_play_store=True,
            artifact_path=artifact_path,
            notes=notes,
        )

    def _assert_play_store_bootstrap_target(
        self,
        *,
        metadata: AndroidAvdMetadata,
        expected_system_image_package: str | None = None,
    ) -> None:
        if not metadata.exists:
            raise AndroidToolingError(
                f"Bootstrap AVD is missing after creation attempt: {metadata.avd_name}"
            )
        if not metadata.play_store_enabled:
            raise AndroidToolingError(
                "Bootstrap AVD is not Play Store-enabled. "
                f"Delete/recreate {metadata.avd_name} with a google_apis_playstore image."
            )
        if expected_system_image_package:
            expected_fragment = expected_system_image_package.replace(";", "/") + "/"
            if metadata.system_image_dir and metadata.system_image_dir != expected_fragment:
                raise AndroidToolingError(
                    "Bootstrap AVD uses an unexpected system image. "
                    f"Expected {expected_fragment}, got {metadata.system_image_dir}."
                )

    def _has_package_sync(self, adb_serial: str, package_name: str) -> bool:
        adb_bin = require_tool_path("adb")
        result = subprocess.run(
            [adb_bin, "-s", adb_serial, "shell", "pm", "path", package_name],
            capture_output=True,
            text=True,
            env=build_android_runtime_env(),
            check=False,
            timeout=30,
        )
        return result.returncode == 0 and result.stdout.strip().startswith("package:")

    def _open_play_store_youtube_page_sync(self, adb_serial: str) -> bool:
        adb_bin = require_tool_path("adb")
        command = (
            f"{shlex.quote(adb_bin)} -s {shlex.quote(adb_serial)} "
            "shell \"am start -W -a android.intent.action.VIEW "
            "-d 'market://details?id=com.google.android.youtube' com.android.vending\""
        )
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            env=build_android_runtime_env(),
            check=False,
            shell=True,
            timeout=60,
        )
        return result.returncode == 0

    def _capture_prepare_artifacts_sync(self, adb_serial: str) -> dict[str, object]:
        adb_bin = require_tool_path("adb")
        artifacts_dir = self._phase_artifacts_dir("prepare")
        screen_path = artifacts_dir / "prepare_screen.png"
        ui_dump_path = artifacts_dir / "prepare_window_dump.xml"
        self._capture_adb_screen_sync(adb_bin, adb_serial, screen_path)
        page_source = self._capture_adb_uiautomator_dump_sync(
            adb_bin,
            adb_serial,
            ui_dump_path,
        )
        sign_in_required = "Sign in" in page_source and "com.android.vending" in page_source
        notes = [f"prepare_screen:{screen_path.name}"]
        if sign_in_required:
            notes.append("play_store_sign_in_required")
        return {
            "extra": {
                "artifacts_dir": str(artifacts_dir),
                "screen_path": str(screen_path),
                "ui_dump_path": str(ui_dump_path),
                "play_store_sign_in_required": sign_in_required,
            },
            "notes": notes,
        }

    def _capture_finalize_artifacts_sync(
        self,
        driver: object,
        suffix: str,
    ) -> dict[str, object]:
        artifacts_dir = self._phase_artifacts_dir("finalize")
        screenshot_path = artifacts_dir / f"youtube_{suffix}.png"
        page_source_path = artifacts_dir / f"youtube_{suffix}.xml"

        try:
            driver.save_screenshot(str(screenshot_path))  # type: ignore[attr-defined]
        except Exception:
            screenshot_path.unlink(missing_ok=True)

        try:
            page_source = driver.page_source or ""  # type: ignore[attr-defined]
            page_source_path.write_text(page_source, encoding="utf-8")
        except Exception:
            page_source_path.unlink(missing_ok=True)

        return {
            "artifacts_dir": str(artifacts_dir),
            "screenshot_path": str(screenshot_path) if screenshot_path.exists() else None,
            "page_source_path": str(page_source_path) if page_source_path.exists() else None,
        }

    def _capture_adb_screen_sync(self, adb_bin: str, adb_serial: str, target_path: Path) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with target_path.open("wb") as file_handle:
            result = subprocess.run(
                [adb_bin, "-s", adb_serial, "exec-out", "screencap", "-p"],
                stdout=file_handle,
                stderr=subprocess.PIPE,
                env=build_android_runtime_env(),
                check=False,
                timeout=60,
            )
        if result.returncode != 0 or target_path.stat().st_size == 0:
            target_path.unlink(missing_ok=True)
            raise AndroidToolingError(
                f"Failed to capture bootstrap screen: {result.stderr.decode('utf-8', errors='ignore').strip()}"
            )

    def _capture_adb_uiautomator_dump_sync(
        self,
        adb_bin: str,
        adb_serial: str,
        target_path: Path,
    ) -> str:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        dump_result = subprocess.run(
            [adb_bin, "-s", adb_serial, "shell", "uiautomator", "dump", "/sdcard/window_dump.xml"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=build_android_runtime_env(),
            check=False,
            timeout=60,
        )
        if dump_result.returncode != 0:
            raise AndroidToolingError(
                "Failed to create bootstrap UI dump: "
                f"{dump_result.stderr.decode('utf-8', errors='ignore').strip()}"
            )

        pull_result = subprocess.run(
            [adb_bin, "-s", adb_serial, "pull", "/sdcard/window_dump.xml", str(target_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=build_android_runtime_env(),
            check=False,
            timeout=60,
        )
        if pull_result.returncode != 0 or not target_path.exists():
            raise AndroidToolingError(
                "Failed to pull bootstrap UI dump: "
                f"{pull_result.stderr.decode('utf-8', errors='ignore').strip()}"
            )
        return target_path.read_text(encoding="utf-8", errors="ignore")

    def _phase_artifacts_dir(self, phase: str) -> Path:
        base_dir = (
            self._config.storage.base_path
            / self._config.android_app.bootstrap_artifacts_subdir
        )
        base_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        phase_dir = base_dir / f"{phase}_{timestamp}"
        phase_dir.mkdir(parents=True, exist_ok=True)
        return phase_dir

    def _write_artifact(
        self,
        *,
        avd_name: str,
        adb_serial: str,
        snapshot_name: str,
        created_avd: bool,
        play_store_available: bool,
        opened_play_store: bool,
        notes: list[str],
        phase: str,
        metadata: AndroidAvdMetadata,
        extra: dict[str, object] | None = None,
    ) -> Path:
        base_dir = (
            self._config.storage.base_path
            / self._config.android_app.bootstrap_artifacts_subdir
        )
        base_dir.mkdir(parents=True, exist_ok=True)
        latest_link = base_dir / f"{phase}_latest.json"
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        artifact_path = base_dir / f"{phase}_{timestamp}.json"
        payload = {
            "phase": phase,
            "avd_name": avd_name,
            "adb_serial": adb_serial,
            "snapshot_name": snapshot_name,
            "created_avd": created_avd,
            "play_store_available": play_store_available,
            "opened_play_store": opened_play_store,
            "avd_metadata": {
                "exists": metadata.exists,
                "play_store_enabled": metadata.play_store_enabled,
                "system_image_dir": metadata.system_image_dir,
                "tag_id": metadata.tag_id,
                "tag_display": metadata.tag_display,
                "device_name": metadata.device_name,
                "target": metadata.target,
                "ini_path": str(metadata.ini_path),
                "avd_dir": str(metadata.avd_dir),
            },
            "notes": notes,
            "extra": extra or {},
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        artifact_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            shutil.copyfile(artifact_path, latest_link)
        except Exception:
            pass
        return artifact_path
