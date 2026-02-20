from fastapi import APIRouter


def register_routers(router: APIRouter) -> None:
    from app.api.modules.auth.routes import router as auth_router
    from app.api.modules.users.routes import router as users_router

    router.include_router(auth_router, prefix="/auth", tags=["Auth"])
    router.include_router(users_router, prefix="/users", tags=["Users"])
