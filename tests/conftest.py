from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio

from app.config import Settings
from app.runtime import Runtime
from app.services.plans import seed_default_plans


@pytest_asyncio.fixture
async def runtime(tmp_path) -> AsyncIterator[Runtime]:
    settings = Settings(
        BOT_TOKEN="123456:unit-test-token",
        OWNER_IDS="9001",
        DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path / 'royal-test.db'}",
        STRIPE_SANDBOX_ENABLED=False,
        BIN_LOOKUP_ENABLED=True,
    )
    runtime = Runtime.build(settings)
    await runtime.database.create_schema_for_tests()
    await seed_default_plans(runtime.database.sessions)
    yield runtime
    await runtime.close()
