import pkgutil
from pathlib import Path

import app.settings
from app.settings import APIConfig, Config, JwtConfig, PostgresConfig, RedisConfig

_test_config = Config(
    env="local",
    api=APIConfig(allowed_hosts=["*"]),
    jwt=JwtConfig(secret_key="test-secret-key-for-testing-only"),
    postgres=PostgresConfig(user="test", password="test", host="localhost", db="test"),
    redis=RedisConfig(host="localhost"),
)

app.settings.get_config = lambda: _test_config

_FIXTURES_ROOT = Path(__file__).parent / "fixtures"

# Collect all fixture modules
fixture_modules = []
for mod in pkgutil.walk_packages([_FIXTURES_ROOT.as_posix()], prefix="tests.fixtures."):
    if not mod.ispkg:
        fixture_modules.append(mod.name)

# Also include package __init__ files that contain fixtures
fixture_modules.extend(
    [
        "tests.fixtures.auth",
        "tests.fixtures.users.admin",
    ]
)

pytest_plugins = fixture_modules
