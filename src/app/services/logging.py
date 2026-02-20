import logging
from typing import Literal

LOG_FORMAT_DEBUG = (
    "[%(levelname)7s]: %(name)s - %(message)s --- %(pathname)s:%(lineno)d"
)
LOG_FORMAT_PROD = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def setup_logging(env: Literal["local", "dev", "prod"]) -> None:
    """Setup logging configuration based on the environment."""
    if env in ("local", "dev"):
        logging.basicConfig(level=logging.DEBUG, format=LOG_FORMAT_DEBUG)
        logging.info("Logging is set to DEBUG level")
    else:
        logging.basicConfig(level=logging.INFO, format=LOG_FORMAT_PROD)
        logging.info("Logging is set to INFO level")

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
