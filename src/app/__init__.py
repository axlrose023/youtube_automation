import uvicorn


def main() -> None:
    from app.settings import get_config

    config = get_config()
    reload = config.env == "local"
    uvicorn.run(
        "app.application:get_production_app",
        host=config.api.host,
        port=config.api.port,
        reload=reload,
        factory=True,
        reload_dirs=["src/app/"],
    )
