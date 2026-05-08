from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.settings import get_config

config = get_config()
engine = create_async_engine(
    config.database_url,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=1800,
)

SessionFactory = async_sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)
