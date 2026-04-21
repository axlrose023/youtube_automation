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


if __name__ == "__main__":
    app()
