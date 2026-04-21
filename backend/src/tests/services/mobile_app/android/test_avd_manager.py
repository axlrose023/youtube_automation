from __future__ import annotations

import pytest

from app.services.mobile_app.android.avd_manager import AndroidAvdManager


@pytest.mark.asyncio
async def test_wait_for_new_serial_accepts_reused_serial_for_same_avd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = AndroidAvdManager(
        emulator_start_timeout_seconds=5,
        device_ready_timeout_seconds=5,
    )

    current_serials = [{"emulator-5554"}]
    run_calls: list[tuple[str, ...]] = []

    async def fake_list_all_emulator_serials(_adb_bin: str) -> list[str]:
        return sorted(current_serials[0])

    async def fake_run(*args: str, **kwargs: object):
        run_calls.append(args)
        return type(
            "CompletedProcess",
            (),
            {
                "returncode": 0,
                "stdout": "yt_android_playstore_api35_clean\r\nOK\r\n",
                "stderr": "",
            },
        )()

    monkeypatch.setattr(manager, "_list_all_emulator_serials", fake_list_all_emulator_serials)
    monkeypatch.setattr(manager, "_run", fake_run)

    serial = await manager._wait_for_new_serial(
        "adb",
        {"emulator-5554"},
        process=None,
        avd_name="yt_android_playstore_api35_clean",
    )

    assert serial == "emulator-5554"
    assert run_calls == [
        ("adb", "-s", "emulator-5554", "emu", "avd", "name"),
    ]
