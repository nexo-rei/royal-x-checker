from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import Settings
from app.database.session import Database
from app.integrations.binlist import BinlistProvider
from app.integrations.stripe_sandbox import StripeSandboxProvider
from app.services.admin import AdminService
from app.services.bin_lookup import BinLookupService
from app.services.credits import CreditService
from app.services.features import FeatureService
from app.services.feedback import FeedbackService
from app.services.fraud_detection import FraudDetectionService
from app.services.history import HistoryService
from app.services.plans import PlanService, seed_default_plans
from app.services.rate_limit import RateLimiter
from app.services.simulator import TransactionSimulator
from app.services.synthetic_data import SyntheticDataGenerator
from app.services.users import UserService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Runtime:
    settings: Settings
    database: Database
    users: UserService
    credits: CreditService
    plans: PlanService
    features: FeatureService
    feedback: FeedbackService
    admin: AdminService
    history: HistoryService
    bin_lookup: BinLookupService
    bin_provider: BinlistProvider
    stripe_provider: StripeSandboxProvider
    simulator: TransactionSimulator
    synthetic: SyntheticDataGenerator
    fraud: FraudDetectionService
    rate_limiter: RateLimiter

    @classmethod
    def build(cls, settings: Settings) -> Runtime:
        database = Database(settings.database_url)
        detector = FraudDetectionService(
            high_amount_threshold=settings.high_amount_threshold,
            repeated_attempt_threshold=settings.repeated_attempt_threshold,
            frequency_threshold=settings.frequency_threshold,
            location_change_window_minutes=settings.location_change_window_minutes,
        )
        bin_provider = BinlistProvider(
            enabled=settings.bin_lookup_enabled,
            timeout_seconds=settings.bin_lookup_timeout_seconds,
        )
        return cls(
            settings=settings,
            database=database,
            users=UserService(database.sessions, settings.free_initial_credits),
            credits=CreditService(database.sessions, settings.feature_costs),
            plans=PlanService(database.sessions),
            features=FeatureService(database.sessions, settings),
            feedback=FeedbackService(database.sessions, settings.max_feedback_chars),
            admin=AdminService(database.sessions),
            history=HistoryService(database.sessions),
            bin_lookup=BinLookupService(bin_provider, settings.bin_lookup_cache_seconds),
            bin_provider=bin_provider,
            stripe_provider=StripeSandboxProvider(settings),
            simulator=TransactionSimulator(
                settings.simulation_rules, settings.max_simulation_amount_minor
            ),
            synthetic=SyntheticDataGenerator(),
            fraud=detector,
            rate_limiter=RateLimiter(settings.rate_limit_count, settings.rate_limit_window_seconds),
        )

    async def startup(self) -> None:
        await self.database.migrate()
        await seed_default_plans(self.database.sessions)
        recovered = await self.credits.recover_stale_operations(older_than_seconds=0)
        if recovered:
            logger.warning("Recovered %s stale operations for idempotent review", recovered)
        if self.settings.stripe_sandbox_enabled and self.settings.stripe_secret_key is None:
            logger.warning(
                "Stripe sandbox enabled without STRIPE_SECRET_KEY; provider operations remain unavailable"
            )
        if self.settings.stripe_secret_key is not None and not self.settings.stripe_sandbox_enabled:
            logger.info(
                "Stripe test key is present, but STRIPE_SANDBOX_ENABLED is false; requests are disabled"
            )

    async def close(self) -> None:
        await self.database.dispose()
