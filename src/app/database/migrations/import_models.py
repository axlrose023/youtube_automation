from app.settings import PathsConfig


def import_models(config: PathsConfig) -> None:
    """Import all models for Alembic migrations."""
    # for path in config.models_path.glob("*.py"):
    #     if path.name != "__init__.py":
    #         __import__(f"app.database.models.{path.stem}")
    for path in config.modules_path.glob("**/models.py"):
        module_path = (
            path.relative_to(config.src_path)
            .with_suffix("")
            .as_posix()
            .replace("/", ".")
        )
        __import__(f"{module_path}")
