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
) -> None:
    """Create a new user."""

    async def _create_user():
        from passlib.context import CryptContext

        pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        container = get_async_container()
        async with container() as request_container:
            uow = await request_container.get(UnitOfWork)
            hashed_password = pwd_context.hash(password)
            user = User(
                username=username,
                password=hashed_password,
                is_active=True,
            )
            await uow.users.create(user)
            await uow.commit()
            typer.echo(f"User '{username}' created successfully.")

    anyio.run(_create_user)
