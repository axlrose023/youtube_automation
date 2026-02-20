from collections.abc import AsyncIterator

from dishka import AsyncContainer, Provider, Scope, make_async_container, provide
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.modules.auth.service import AuthService
from app.api.modules.auth.services import JwtService
from app.api.modules.users.service import UserService
from app.clients.providers import HttpClientsProvider
from app.database.engine import SessionFactory
from app.database.uow import UnitOfWork
from app.settings import Config, get_config

try:
    from app.services.browser import (
        AdsPowerSessionProvider,
        BrowserSessionProvider,
        ChromiumSessionProvider,
        UserAgentProvider,
    )

    _BROWSER_AVAILABLE = True
except ModuleNotFoundError:
    _BROWSER_AVAILABLE = False


class AppProvider(Provider):
    @provide(scope=Scope.APP)
    def get_config(self) -> Config:
        return get_config()

    @provide(scope=Scope.REQUEST)
    async def get_session(self) -> AsyncIterator[AsyncSession]:
        async with SessionFactory() as session:
            yield session

    @provide(scope=Scope.REQUEST)
    async def get_uow(self, session: AsyncSession) -> AsyncIterator[UnitOfWork]:
        async with UnitOfWork(session) as uow:
            yield uow


class ServicesProvider(Provider):
    @provide(scope=Scope.APP)
    def get_jwt_service(self, config: Config) -> JwtService:
        return JwtService(config)

    @provide(scope=Scope.REQUEST)
    async def get_auth_service(
        self, uow: UnitOfWork, jwt_service: JwtService
    ) -> AuthService:
        return AuthService(uow, jwt_service)

    @provide(scope=Scope.REQUEST)
    async def get_user_service(
        self, uow: UnitOfWork, auth_service: AuthService
    ) -> UserService:
        return UserService(uow, auth_service)


if _BROWSER_AVAILABLE:
    class BrowserDIProvider(Provider):
        @provide(scope=Scope.APP)
        async def get_browser_session_provider(
            self, config: Config
        ) -> AsyncIterator[BrowserSessionProvider]:
            if config.browser_backend == "adspower":
                provider = AdsPowerSessionProvider(config=config.adspower)
            else:
                provider = ChromiumSessionProvider(
                    playwright_config=config.playwright,
                    viewport_config=config.viewport,
                    user_agent_provider=UserAgentProvider(config.useragent),
                )
            await provider.start()
            yield provider
            await provider.stop()


def get_async_container() -> AsyncContainer:
    providers: list[Provider] = [
        AppProvider(),
        ServicesProvider(),
        HttpClientsProvider(),
    ]
    if _BROWSER_AVAILABLE:
        providers.append(BrowserDIProvider())
    return make_async_container(*providers)
