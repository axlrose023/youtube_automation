from collections.abc import AsyncIterator

from dishka import AsyncContainer, Provider, Scope, make_async_container, provide
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.modules.emulation.service import (
    EmulationHistoryService,
    EmulationSessionService,
)
from app.api.modules.users.service import AuthService, UserService
from app.api.modules.users.services.jwt import JwtService
from app.clients.providers import HttpClientsProvider
from app.database.engine import SessionFactory
from app.database.uow import UnitOfWork
from app.services.emulation.core.capture_factory import (
    AdCaptureProviderFactory,
    DefaultAdCaptureProviderFactory,
)
from app.services.emulation.orchestration.scheduler import EmulationOrchestrationService
from app.services.emulation.persistence import EmulationPersistenceService
from app.services.emulation.session.store import EmulationSessionStore
from app.settings import Config, get_config

try:
    from app.api.modules.browser.service import BrowserService
    from app.services.browser import (
        AdsPowerSessionProvider,
        BrowserSessionProvider,
        ChromiumSessionProvider,
        UserAgentProvider,
    )

    _BROWSER_AVAILABLE = True
except ModuleNotFoundError:
    _BROWSER_AVAILABLE = False

try:
    from app.clients.gemini import GeminiClient
    from app.services.emulation.ads.analysis.service import AdAnalysisService
    from app.services.emulation.media_storage import LocalMediaStorage, MediaStorage
    from app.services.emulation.ads.analysis.sampler import AdAnalysisVideoSampler

    _GEMINI_AVAILABLE = True
except ModuleNotFoundError:
    _GEMINI_AVAILABLE = False


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

    @provide(scope=Scope.REQUEST)
    async def get_emulation_history_service(
        self, uow: UnitOfWork
    ) -> EmulationHistoryService:
        return EmulationHistoryService(uow)

    @provide(scope=Scope.REQUEST)
    async def get_emulation_session_service(
        self,
        session_store: EmulationSessionStore,
        history_service: EmulationHistoryService,
    ) -> EmulationSessionService:
        return EmulationSessionService(session_store, history_service)

    @provide(scope=Scope.REQUEST)
    async def get_emulation_persistence_service(
        self, uow: UnitOfWork
    ) -> EmulationPersistenceService:
        return EmulationPersistenceService(uow)

    @provide(scope=Scope.REQUEST)
    async def get_emulation_orchestration_service(
        self,
        session_store: EmulationSessionStore,
        persistence: EmulationPersistenceService,
    ) -> EmulationOrchestrationService:
        return EmulationOrchestrationService(session_store, persistence)


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

        @provide(scope=Scope.REQUEST)
        def get_browser_service(
            self, session_provider: BrowserSessionProvider
        ) -> BrowserService:
            return BrowserService(session_provider)


class EmulationDIProvider(Provider):
    @provide(scope=Scope.APP)
    async def get_redis(self, config: Config) -> AsyncIterator[Redis]:
        r = Redis.from_url(config.redis_url)
        yield r
        await r.aclose()

    @provide(scope=Scope.APP)
    def get_session_store(self, redis: Redis) -> EmulationSessionStore:
        return EmulationSessionStore(redis)

    @provide(scope=Scope.APP)
    def get_ad_capture_factory(self) -> AdCaptureProviderFactory:
        return DefaultAdCaptureProviderFactory()



if _GEMINI_AVAILABLE:

    class GeminiDIProvider(Provider):
        @provide(scope=Scope.APP)
        def get_gemini_client(self, config: Config) -> GeminiClient:
            return GeminiClient(api_key=config.gemini.api_key, model=config.gemini.model)

        @provide(scope=Scope.APP)
        def get_media_storage(self, config: Config) -> MediaStorage:
            return LocalMediaStorage(config.storage.ad_captures_path)

        @provide(scope=Scope.APP)
        def get_ad_analysis_video_sampler(self) -> AdAnalysisVideoSampler:
            return AdAnalysisVideoSampler()

        @provide(scope=Scope.REQUEST)
        async def get_ad_analysis_service(
            self, gemini: GeminiClient, uow: UnitOfWork, config: Config,
            storage: MediaStorage,
            video_sampler: AdAnalysisVideoSampler,
        ) -> AdAnalysisService:
            return AdAnalysisService(
                gemini,
                uow,
                config.storage.ad_captures_path,
                storage,
                video_sampler,
            )


def get_async_container() -> AsyncContainer:
    providers: list[Provider] = [
        AppProvider(),
        ServicesProvider(),
        HttpClientsProvider(),
        EmulationDIProvider(),
    ]
    if _BROWSER_AVAILABLE:
        providers.append(BrowserDIProvider())
    if _GEMINI_AVAILABLE:
        providers.append(GeminiDIProvider())
    return make_async_container(*providers)
