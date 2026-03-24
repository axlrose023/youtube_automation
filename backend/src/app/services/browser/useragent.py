import logging

from fake_useragent import UserAgent

from app.settings import UserAgentConfig, get_config

logger = logging.getLogger(__name__)


class UserAgentProvider:
    def __init__(self, config: UserAgentConfig | None = None) -> None:
        self._config = config or get_config().useragent
        try:
            self._ua = UserAgent(browsers=self._config.browsers)
        except Exception as e:
            logger.warning("Failed to initialize UserAgent: %s", e)
            self._ua = None

    def get(self) -> str:
        if self._ua:
            try:
                return self._ua.random
            except Exception:
                pass
        return self._config.fallback
