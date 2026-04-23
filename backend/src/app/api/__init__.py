from fastapi import APIRouter


def register_routers(router: APIRouter) -> None:
    from app.api.modules.browser.routes import router as browser_router
    from app.api.modules.emulation.routes import router as emulation_router, public_router as emulation_public_router
    from app.api.modules.proxies.routes import router as proxies_router
    from app.api.modules.users.routes import auth_router, router as users_router

    router.include_router(auth_router, prefix="/auth", tags=["Auth"])
    router.include_router(users_router, prefix="/users", tags=["Users"])
    router.include_router(browser_router, prefix="/browser", tags=["Browser"])
    router.include_router(emulation_public_router, prefix="/emulation", tags=["Emulation"])
    router.include_router(emulation_router, prefix="/emulation", tags=["Emulation"])
    router.include_router(proxies_router, prefix="/proxies", tags=["Proxies"])
