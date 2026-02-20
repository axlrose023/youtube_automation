from dishka.integrations.taskiq import setup_dishka
from taskiq import TaskiqScheduler
from taskiq.schedule_sources import LabelScheduleSource
from taskiq_redis import ListQueueBroker, RedisAsyncResultBackend

from app.ioc import get_async_container
from app.services.logging import setup_logging
from app.settings import get_config

config = get_config()
setup_logging(config.env)

redis_async_result: RedisAsyncResultBackend = RedisAsyncResultBackend(
    redis_url=config.redis_url,
)

broker = ListQueueBroker(url=config.redis_url)
broker.with_result_backend(redis_async_result)

scheduler = TaskiqScheduler(
    broker=broker,
    sources=[LabelScheduleSource(broker)],
)

container = get_async_container()
setup_dishka(container=container, broker=broker)
