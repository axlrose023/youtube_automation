import bcrypt
import pytest_asyncio

from app.api.common.utils import build_filters
from app.api.modules.users.models import User
from app.database.uow import UnitOfWork


@pytest_asyncio.fixture
async def user(uow: UnitOfWork) -> User:
    username = "admin"
    filters = build_filters(User, {"username": username})
    users = await uow.users.get_all(limit=1, offset=0, filters=filters)
    if users:
        return users[0]

    hashed_password = bcrypt.hashpw(b"admin123", bcrypt.gensalt(rounds=12)).decode()

    user = User(
        username=username,
        password=hashed_password,
        is_active=True,
    )
    await uow.users.create(user)
    await uow.commit()
    return user
