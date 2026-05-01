from configparser import ConfigParser
from pathlib import Path
from typing import Annotated

import anyio
import typer
from alembic import command
from alembic.config import Config

from app.api.modules.users.models import User
from app.database.uow import UnitOfWork
from app.ioc import get_async_container
from app.services.logging import setup_logging
from app.settings import get_config

setup_logging(get_config().env)

app = typer.Typer()


alembic_ini_path = Path(__file__).parent.parent.parent / "alembic.ini"


def get_alembic_config() -> Config:
    if not alembic_ini_path.exists():
        raise FileNotFoundError("alembic.ini not found")
    return Config("alembic.ini")


@app.command("migration")
def migration(name: Annotated[str | None, typer.Option(prompt=True)] = None) -> None:
    """Generate a new Alembic migration."""
    alembic_cfg = get_alembic_config()
    command.revision(alembic_cfg, message=name, autogenerate=True)
    typer.echo(
        typer.style(
            f"New migration '{name}' created successfully.",
            fg=typer.colors.GREEN,
        ),
    )


@app.command("migrations")
def migrations() -> None:
    """list migration files."""
    if not alembic_ini_path.exists():
        raise FileNotFoundError("alembic.ini not found")
    config = ConfigParser()
    config.read(alembic_ini_path)
    migrations_path = config.get("alembic", "script_location")
    typer.echo(f"Migration files are located in: {migrations_path}")
    migration_dir = Path(migrations_path) / "versions"
    for file in migration_dir.glob("*.py"):
        typer.echo(f"- {file}")


@app.command("upgrade")
def upgrade(revision: str = "head") -> None:
    """Upgrade the database to a specific revision."""
    alembic_cfg = get_alembic_config()
    command.upgrade(alembic_cfg, revision)
    typer.echo(
        typer.style(
            f"Database upgraded to revision '{revision}' successfully.",
            fg=typer.colors.GREEN,
        ),
    )


@app.command("downgrade")
def downgrade(revision: str = "-1") -> None:
    """Downgrade the database to a specific revision."""
    alembic_cfg = get_alembic_config()
    command.downgrade(alembic_cfg, revision)
    typer.echo(
        typer.style(
            f"Database downgraded to revision '{revision}' successfully.",
            fg=typer.colors.GREEN,
        ),
    )


@app.command("create_user")
def create_user(
    username: Annotated[str, typer.Option(prompt=True)] = None,
    password: Annotated[str, typer.Option(prompt=True, hide_input=True)] = None,
    admin: Annotated[bool, typer.Option("--admin")] = False,
) -> None:
    """Create a new user. Use --admin to create an admin user."""

    async def _create_user():
        import bcrypt

        container = get_async_container()
        async with container() as request_container:
            uow = await request_container.get(UnitOfWork)
            hashed_password = bcrypt.hashpw(
                password.encode("utf-8"), bcrypt.gensalt(rounds=12)
            ).decode("utf-8")
            user = User(
                username=username,
                password=hashed_password,
                is_active=True,
                is_admin=admin,
            )
            await uow.users.create(user)
            await uow.commit()
            role = "admin" if admin else "user"
            typer.echo(f"User '{username}' ({role}) created successfully.")

    anyio.run(_create_user)


@app.command("ensure_user")
def ensure_user(
    username: Annotated[str, typer.Option()] = ...,
    password: Annotated[str, typer.Option(hide_input=True)] = ...,
    admin: Annotated[bool, typer.Option("--admin")] = False,
    active: Annotated[bool, typer.Option("--active/--inactive")] = True,
) -> None:
    """Create or update a user without interactive prompts."""

    async def _ensure_user() -> None:
        from app.api.common.utils import build_filters
        import bcrypt

        container = get_async_container()
        async with container() as request_container:
            uow = await request_container.get(UnitOfWork)
            hashed_password = bcrypt.hashpw(
                password.encode("utf-8"), bcrypt.gensalt(rounds=12)
            ).decode("utf-8")
            users = await uow.users.get_all(
                limit=1,
                offset=0,
                filters=build_filters(User, {"username": username}),
            )
            existing = users[0] if users else None

            if existing is None:
                user = User(
                    username=username,
                    password=hashed_password,
                    is_active=active,
                    is_admin=admin,
                    is_deleted=False,
                )
                await uow.users.create(user)
                action = "created"
            else:
                await uow.users.update(
                    existing.id,
                    password=hashed_password,
                    is_active=active,
                    is_admin=admin,
                    is_deleted=False,
                )
                action = "updated"

            await uow.commit()
            role = "admin" if admin else "user"
            typer.echo(f"User '{username}' ({role}) {action} successfully.")

    anyio.run(_ensure_user)


@app.command("android_probe")
def android_probe(
    topic: Annotated[str, typer.Option(prompt=True)] = "forex investing",
    avd_name: Annotated[str | None, typer.Option()] = None,
    proxy_url: Annotated[str | None, typer.Option()] = None,
    adspower_profile_id: Annotated[str | None, typer.Option()] = None,
    headless: Annotated[bool, typer.Option("--headless/--visible")] = False,
) -> None:
    """Run a minimal Android YouTube probe on the configured AVD/Appium stack."""

    from app.services.mobile_app.android.runner import AndroidYouTubeProbeRunner

    async def _run_probe() -> None:
        result = await AndroidYouTubeProbeRunner(get_config()).run(
            topic=topic,
            avd_name=avd_name,
            proxy_url=proxy_url,
            adspower_profile_id=adspower_profile_id,
            headless=headless,
        )
        typer.echo(
            typer.style(
                f"Android probe completed. "
                f"AVD={result.avd_name} serial={result.adb_serial} "
                f"title={result.opened_title or '<none>'} "
                f"watch_verified={result.watch_verified} "
                f"watch_ad_detected={result.watch_ad_detected} "
                f"ad_cta_clicked={result.ad_cta_clicked} "
                f"artifact={result.artifact_path}",
                fg=typer.colors.GREEN,
            )
        )

    anyio.run(_run_probe)


@app.command("android_session_run")
def android_session_run(
    topic: Annotated[list[str], typer.Option("--topic")] = [],
    duration_minutes: Annotated[int | None, typer.Option("--duration-minutes")] = None,
    avd_name: Annotated[str | None, typer.Option()] = None,
    proxy_url: Annotated[str | None, typer.Option()] = None,
    adspower_profile_id: Annotated[str | None, typer.Option()] = None,
    headless: Annotated[bool, typer.Option("--headless/--visible")] = False,
) -> None:
    """Run a multi-topic Android YouTube session on a single AVD/Appium lifecycle."""

    from app.services.mobile_app.android.runner import AndroidYouTubeSessionRunner

    async def _run_session() -> None:
        result = await AndroidYouTubeSessionRunner(get_config()).run(
            topics=topic,
            duration_minutes=duration_minutes,
            avd_name=avd_name,
            proxy_url=proxy_url,
            adspower_profile_id=adspower_profile_id,
            headless=headless,
        )
        typer.echo(
            typer.style(
                f"Android session completed. "
                f"AVD={result.avd_name} serial={result.adb_serial} "
                f"topics={len(result.topics)} "
                f"duration_minutes_target={result.duration_minutes_target or 0} "
                f"elapsed_seconds={result.elapsed_seconds} "
                f"verified={sum(1 for item in result.topic_results if item.watch_verified)} "
                f"ads={len(result.watched_ads)} "
                f"artifact={result.artifact_path}",
                fg=typer.colors.GREEN,
            )
        )

    anyio.run(_run_session)


@app.command("android_bootstrap_warm_snapshot")
def android_bootstrap_warm_snapshot(
    avd_name: Annotated[str | None, typer.Option()] = None,
    system_image_package: Annotated[str | None, typer.Option()] = None,
    device_preset: Annotated[str | None, typer.Option()] = None,
    snapshot_name: Annotated[str | None, typer.Option()] = None,
    stop_after_save: Annotated[bool, typer.Option("--stop-after-save/--keep-open")] = True,
) -> None:
    """Create or launch a visible Play Store AVD, update YouTube manually, then save a warm snapshot."""
    from app.services.mobile_app.android.bootstrap import (
        AndroidWarmSnapshotBootstrapper,
    )

    config = get_config()
    bootstrapper = AndroidWarmSnapshotBootstrapper(config)

    async def _prepare():
        return await bootstrapper.prepare(
            avd_name=avd_name,
            system_image_package=system_image_package,
            device_preset=device_preset,
        )

    prepared = anyio.run(_prepare)
    typer.echo(
        typer.style(
            f"AVD ready. avd={prepared.avd_name} serial={prepared.adb_serial} "
            f"play_store={prepared.play_store_available} artifact={prepared.artifact_path}",
            fg=typer.colors.GREEN,
        )
    )
    typer.echo(
        "Update YouTube inside the visible emulator, open the app once, "
        "and make sure the mandatory update screen is gone."
    )
    typer.prompt("Press Enter after YouTube is updated", default="", show_default=False)

    async def _finalize():
        return await bootstrapper.finalize(
            avd_name=avd_name,
            snapshot_name=snapshot_name,
            stop_after_save=stop_after_save,
        )

    finalized = anyio.run(_finalize)
    typer.echo(
        typer.style(
            f"Warm snapshot saved. avd={finalized.avd_name} "
            f"snapshot={finalized.snapshot_name} artifact={finalized.artifact_path}",
            fg=typer.colors.GREEN,
        )
    )


@app.command("android_manual_debug")
def android_manual_debug(
    avd_name: Annotated[str | None, typer.Option()] = None,
    proxy_url: Annotated[str | None, typer.Option()] = None,
    headless: Annotated[bool, typer.Option("--headless/--visible")] = False,
    snapshot_dir: Annotated[str | None, typer.Option()] = None,
    stop_after: Annotated[bool, typer.Option("--stop-after/--keep-open")] = True,
) -> None:
    """Start AVD + Appium + YouTube, then wait for manual 'capture' commands.

    Usage: type 'c' + Enter to snapshot current screen and run ad detection.
    Type 'q' + Enter to quit.
    """
    import datetime
    import json
    import subprocess
    from pathlib import Path

    from app.services.mobile_app.android.analysis import AndroidAdAnalysisCoordinator
    from app.services.mobile_app.android.avd_manager import AndroidEmulatorLaunchOptions
    from app.services.mobile_app.android.landing_scraper import AndroidLandingPageScraper
    from app.services.mobile_app.android.runner import AndroidYouTubeSessionRunner
    from app.services.mobile_app.android.runtime import build_android_probe_runtime
    from app.services.mobile_app.android.youtube.navigator import AndroidYouTubeNavigator
    from app.services.mobile_app.android.youtube.watcher import AndroidYouTubeWatcher

    config = get_config()
    out_dir = (
        Path(snapshot_dir)
        if snapshot_dir is not None
        else config.storage.base_path / "android_manual_debug"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    runner = AndroidYouTubeSessionRunner(config)
    runtime = build_android_probe_runtime(config.android_app)
    resolved_avd = avd_name or config.android_app.default_avd_name

    async def _start():
        emulator_proxy, host_proxy, proxy_notes, bridge = await runner._prepare_emulator_proxy(
            proxy_url=proxy_url, adspower_profile_id=None,
        )
        device = await runtime.avd_manager.ensure_device(
            avd_name=resolved_avd,
            launch=AndroidEmulatorLaunchOptions(
                headless=headless,
                gpu_mode=config.android_app.emulator_gpu_mode,
                accel_mode=config.android_app.emulator_accel_mode,
                http_proxy=emulator_proxy,
                load_snapshot=config.android_app.emulator_use_snapshots,
                save_snapshot=False,
                snapshot_name=(
                    config.android_app.runtime_snapshot_name
                    if config.android_app.emulator_use_snapshots else None
                ),
                force_snapshot_load=config.android_app.emulator_use_snapshots,
                skip_adb_auth=config.android_app.emulator_skip_adb_auth,
                force_stop_running=False,
            ),
        )
        session = await runtime.appium_provider.create_youtube_session(
            adb_serial=device.adb_serial,
            avd_name=device.avd_name,
        )
        navigator = AndroidYouTubeNavigator(
            session.driver, config.android_app, adb_serial=device.adb_serial,
        )
        await navigator.ensure_app_ready()
        return device, session, navigator, host_proxy, proxy_notes, bridge

    def _ad_summary(ad: dict) -> dict:
        capture = ad.get("capture") if isinstance(ad.get("capture"), dict) else {}
        analysis_summary = (
            capture.get("analysis_summary")
            if isinstance(capture, dict)
            else ad.get("analysis_summary")
        )
        return {
            "position": ad.get("position"),
            "ad_type": ad.get("ad_type"),
            "advertiser_domain": ad.get("advertiser_domain"),
            "display_url": ad.get("display_url"),
            "headline_text": ad.get("headline_text"),
            "cta_text": ad.get("cta_text"),
            "cta_href": ad.get("cta_href"),
            "sponsor_label": ad.get("sponsor_label"),
            "video_status": capture.get("video_status") if isinstance(capture, dict) else None,
            "video_file": capture.get("video_file") if isinstance(capture, dict) else None,
            "landing_status": capture.get("landing_status") if isinstance(capture, dict) else None,
            "landing_url": capture.get("landing_url") if isinstance(capture, dict) else None,
            "landing_title": capture.get("landing_title") if isinstance(capture, dict) else None,
            "landing_screenshot_path": (
                capture.get("landing_screenshot_path") if isinstance(capture, dict) else None
            ),
            "click_tracking_url": (
                capture.get("click_tracking_url") if isinstance(capture, dict) else None
            ),
            "screenshot_paths": capture.get("screenshot_paths") if isinstance(capture, dict) else [],
            "analysis_status": capture.get("analysis_status") if isinstance(capture, dict) else None,
            "analysis_summary": analysis_summary,
        }

    async def _capture(device, session, navigator, idx: int, host_proxy: str | None) -> dict:
        serial = device.adb_serial
        ts = datetime.datetime.now().strftime("%H%M%S")
        stem = f"snap_{idx:03d}_{ts}"

        png = out_dir / f"{stem}.png"
        with png.open("wb") as screen_file:
            subprocess.run(
                ["adb", "-s", serial, "exec-out", "screencap", "-p"],
                stdout=screen_file,
                check=False,
            )

        xml_path = out_dir / f"{stem}.xml"
        try:
            src = session.driver.page_source
            xml_path.write_text(src, encoding="utf-8")
        except Exception:
            src = ""

        watcher = AndroidYouTubeWatcher(session.driver, config.android_app, adb_serial=serial)
        try:
            snap, _ = await anyio.to_thread.run_sync(
                lambda: watcher._collect_sample_sync(0)
            )
            watcher_result = {
                "ad_detected": snap.ad_detected,
                "player_visible": snap.player_visible,
                "watch_panel_visible": snap.watch_panel_visible,
                "results_visible": snap.results_visible,
                "is_reel_surface": snap.is_reel_surface,
                "skip_available": snap.skip_available,
                "ad_sponsor_label": snap.ad_sponsor_label,
                "ad_headline_text": snap.ad_headline_text,
                "ad_display_url": snap.ad_display_url,
                "ad_cta_text": snap.ad_cta_text,
                "ad_visible_lines": snap.ad_visible_lines,
                "ad_signal_labels": snap.ad_signal_labels,
                "ad_cta_labels": snap.ad_cta_labels,
                "error_messages": snap.error_messages,
            }
        except Exception as e:
            watcher_result = {"error": str(e)}

        ocr_result = {}
        try:
            from app.services.mobile_app.android.youtube.banner_ocr import (
                extract_from_banner_screenshot, is_available as ocr_ok,
            )
            if ocr_ok():
                ocr_domain, ocr_headline = extract_from_banner_screenshot(png)
                ocr_result["ocr_domain"] = ocr_domain
                ocr_result["ocr_headline"] = ocr_headline
            else:
                ocr_result["ocr_available"] = False
        except Exception as e:
            ocr_result["ocr_error"] = str(e)

        topic_notes: list[str] = []
        watched_ads: list[dict[str, object]] = []
        topic_watched_ads: list[dict[str, object]] = []
        ad_analysis = AndroidAdAnalysisCoordinator(config.gemini, config.storage)
        landing_scraper = AndroidLandingPageScraper(config.storage, proxy_url=host_proxy)
        await landing_scraper.start()

        async def _notify_ad_captured() -> None:
            return None

        try:
            feed_card_captured = await runner._capture_feed_sponsored_card_if_present(
                navigator=navigator,
                session=session,
                adb_serial=serial,
                topic=f"manual_capture_{idx:03d}",
                topic_notes=topic_notes,
                watched_ads=watched_ads,
                topic_watched_ads=topic_watched_ads,
                ad_analysis=ad_analysis,
                landing_scraper=landing_scraper,
                notify_ad_captured=_notify_ad_captured,
            )
            search_banner_captured = await runner._capture_search_banner_ad_if_present(
                navigator=navigator,
                session=session,
                adb_serial=serial,
                topic=f"manual_capture_{idx:03d}",
                topic_notes=topic_notes,
                watched_ads=watched_ads,
                topic_watched_ads=topic_watched_ads,
                ad_analysis=ad_analysis,
                landing_scraper=landing_scraper,
                notify_ad_captured=_notify_ad_captured,
            )
            await ad_analysis.drain(timeout_seconds=45.0)
            await landing_scraper.drain(timeout_seconds=45.0)
        finally:
            await landing_scraper.stop()

        result = {
            "captured_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "serial": serial,
            "snapshot": {
                "screenshot": str(png),
                "page_source_xml": str(xml_path),
            },
            "watcher_sample": watcher_result,
            "ocr": ocr_result,
            "feed_sponsored_card_detector": {
                "captured": feed_card_captured,
                "notes": topic_notes,
                "ads": [
                    _ad_summary(ad)
                    for ad in watched_ads
                    if ad.get("ad_type") == "feed_sponsored_card"
                ],
            },
            "search_banner_detector": {
                "captured": search_banner_captured,
                "notes": topic_notes,
                "ads": [
                    _ad_summary(ad)
                    for ad in watched_ads
                    if ad.get("ad_type") == "search_banner"
                ],
            },
        }

        json_path = out_dir / f"{stem}.json"
        result["snapshot"]["json"] = str(json_path)
        json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))

        return result

    async def _main():
        typer.echo(f"Starting AVD={resolved_avd} headless={headless}...")
        device = None
        session = None
        host_proxy = None
        bridge = None
        device, session, navigator, host_proxy, proxy_notes, bridge = await _start()
        typer.echo(
            typer.style(
                f"Ready. serial={device.adb_serial}  snapshots -> {out_dir}\n"
                f"Proxy: {', '.join(proxy_notes) if proxy_notes else 'none'}\n"
                "Commands: [c] capture+detect  [q] quit",
                fg=typer.colors.GREEN,
            )
        )
        idx = 0
        try:
            while True:
                cmd = await anyio.to_thread.run_sync(lambda: input("ad_debug> ").strip().lower())
                if cmd in ("q", "quit", "exit"):
                    break
                if cmd in ("c", "capture", ""):
                    idx += 1
                    typer.echo(f"Capturing snapshot #{idx}...")
                    result = await _capture(device, session, navigator, idx, host_proxy)
                    typer.echo(typer.style(json.dumps(result, ensure_ascii=False, indent=2), fg=typer.colors.CYAN))
                else:
                    typer.echo("Unknown command. Use 'c' to capture, 'q' to quit.")
        finally:
            if session is not None:
                try:
                    await runtime.appium_provider.close_session(session)
                except Exception:
                    pass
            if stop_after and device is not None:
                try:
                    await runtime.avd_manager.stop_device(
                        device.adb_serial,
                        avd_name=device.avd_name,
                    )
                except Exception:
                    try:
                        await runtime.avd_manager.force_cleanup_device(
                            adb_serial=device.adb_serial,
                            avd_name=device.avd_name,
                        )
                    except Exception:
                        pass
            if bridge is not None:
                try:
                    await runner._proxy_bridge.stop(bridge)
                except Exception:
                    pass

    anyio.run(_main)


if __name__ == "__main__":
    app()
