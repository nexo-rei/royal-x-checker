from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.database.session import Database
from app.runtime import Runtime


def test_settings_parse_owner_ids_and_reject_live_stripe_keys() -> None:
    settings = Settings(BOT_TOKEN="123:token", OWNER_IDS="11,22,11")
    assert settings.owner_ids == frozenset({11, 22})
    with pytest.raises(ValidationError):
        Settings(BOT_TOKEN="123:token", OWNER_IDS="11", STRIPE_SECRET_KEY="sk_live_not-allowed")


def test_runtime_configuration_requires_token_and_owner() -> None:
    with pytest.raises(ValueError, match="BOT_TOKEN"):
        Settings().validate_runtime()
    with pytest.raises(ValueError, match="OWNER_IDS"):
        Settings(BOT_TOKEN="123:token").validate_runtime()


async def test_alembic_startup_migrates_database_and_seeds_plans(tmp_path) -> None:
    settings = Settings(
        BOT_TOKEN="123:token",
        OWNER_IDS="42",
        DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path / 'migrated.db'}",
    )
    runtime = Runtime.build(settings)
    try:
        await runtime.startup()
        await runtime.users.ensure_registered(101, "Migration Test")
        profile = await runtime.users.profile(101)
        assert profile is not None
        assert profile.plan_code == "free"
        assert profile.credits == settings.free_initial_credits
        assert {plan.code for plan in await runtime.plans.enabled_plans()} == {
            "free",
            "basic",
            "premium",
        }
    finally:
        await runtime.close()


async def test_in_memory_database_supports_basic_sessions() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    try:
        await database.create_schema_for_tests()
        from app.services.plans import seed_default_plans

        await seed_default_plans(database.sessions)
        from app.services.plans import PlanService

        assert {plan.code for plan in await PlanService(database.sessions).enabled_plans()} == {
            "free",
            "basic",
            "premium",
        }
    finally:
        await database.dispose()
