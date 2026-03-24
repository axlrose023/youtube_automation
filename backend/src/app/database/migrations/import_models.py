from app.settings import PathsConfig


def import_models(config: PathsConfig) -> None:




    for path in config.modules_path.glob("**/models.py"):
        module_path = (
            path.relative_to(config.src_path)
            .with_suffix("")
            .as_posix()
            .replace("/", ".")
        )
        __import__(f"{module_path}")
