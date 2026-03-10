import logging

from app.tiq import broker

logger = logging.getLogger(__name__)


@broker.task(schedule=[{"cron": "* * * * *"}])
def health_check():
    logger.info("Health check task executed.")
