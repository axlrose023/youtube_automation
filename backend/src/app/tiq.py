import os

from dishka.integrations.taskiq import setup_dishka
from taskiq import TaskiqScheduler
from taskiq.schedule_sources import LabelScheduleSource
from taskiq_redis import ListQueueBroker, RedisAsyncResultBackend
from taskiq_redis.list_schedule_source import ListRedisScheduleSource

from app.ioc import get_async_container
from app.services.logging import setup_logging
from app.settings import get_config

config = get_config()
setup_logging(config.env)

DEFAULT_QUEUE_NAME = "taskiq"
EMULATION_QUEUE_NAME = os.getenv("TASKIQ_EMULATION_QUEUE_NAME", "taskiq_emulation")
WORKER_QUEUE_NAME = os.getenv("TASKIQ_QUEUE_NAME", DEFAULT_QUEUE_NAME)
DYNAMIC_SCHEDULE_PREFIX = os.getenv("TASKIQ_DYNAMIC_SCHEDULE_PREFIX", "taskiq_dynamic_schedule")

redis_async_result: RedisAsyncResultBackend = RedisAsyncResultBackend(
    redis_url=config.redis_url,
)

broker = ListQueueBroker(
    url=config.redis_url,
    queue_name=WORKER_QUEUE_NAME,
)
broker.with_result_backend(redis_async_result)

dynamic_schedule_source = ListRedisScheduleSource(
    url=config.redis_url,
    prefix=DYNAMIC_SCHEDULE_PREFIX,
)

scheduler = TaskiqScheduler(
    broker=broker,
    sources=[
        LabelScheduleSource(broker),
        dynamic_schedule_source,
    ],
)

container = get_async_container()
setup_dishka(container=container, broker=broker)
