from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.models import AuditLog, Plan, Subscription, User
from app.services.errors import NotFound

PLAN_FEATURES = [
    "stripe_sandbox",
    "bin_lookup",
    "file_analysis",
    "simulation",
    "synthetic_data",
    "fraud_analysis",
    "feedback",
]
DEFAULT_PLANS = (
    {"code": "free", "name": "Free", "duration_days": 0, "price_minor": 0, "usage_quota": 10},
    {"code": "basic", "name": "Basic", "duration_days": 30, "price_minor": 499, "usage_quota": 100},
    {
        "code": "premium",
        "name": "Premium",
        "duration_days": 30,
        "price_minor": 1499,
        "usage_quota": 500,
    },
)


async def seed_default_plans(sessions: async_sessionmaker) -> None:
    async with sessions.begin() as session:
        existing = set((await session.scalars(select(Plan.code))).all())
        for definition in DEFAULT_PLANS:
            if definition["code"] not in existing:
                session.add(
                    Plan(
                        **definition,
                        currency="USD",
                        allowed_features=PLAN_FEATURES.copy(),
                        enabled=True,
                    )
                )


class PlanService:
    def __init__(self, sessions: async_sessionmaker) -> None:
        self.sessions = sessions

    async def enabled_plans(self) -> list[Plan]:
        async with self.sessions() as session:
            result = await session.scalars(
                select(Plan).where(Plan.enabled.is_(True)).order_by(Plan.price_minor)
            )
            return list(result.all())

    async def get_by_code(self, code: str) -> Plan | None:
        async with self.sessions() as session:
            return await session.scalar(select(Plan).where(Plan.code == code))

    @staticmethod
    async def effective_plan(session: Any, user: User, at: datetime | None = None) -> Plan:
        now = at or datetime.now(UTC)
        subscription = await session.scalar(
            select(Subscription)
            .where(
                Subscription.user_id == user.id,
                Subscription.status == "active",
                Subscription.starts_at <= now,
                Subscription.ends_at > now,
            )
            .order_by(Subscription.ends_at.desc())
            .limit(1)
        )
        if subscription:
            return await session.get(Plan, subscription.plan_id)
        assigned = await session.get(Plan, user.plan_id) if user.plan_id else None
        if assigned and assigned.code == "free":
            return assigned
        free_plan = await session.scalar(select(Plan).where(Plan.code == "free"))
        if free_plan is None:
            raise RuntimeError("The seeded free plan is missing")
        return free_plan

    async def request_activation(
        self, telegram_id: int, plan_code: str, request_key: str
    ) -> tuple[Subscription, bool]:
        async with self.sessions.begin() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            plan = await session.scalar(
                select(Plan).where(Plan.code == plan_code, Plan.enabled.is_(True))
            )
            if user is None or plan is None or plan.code == "free":
                raise NotFound()
            existing = await session.scalar(
                select(Subscription).where(
                    Subscription.user_id == user.id,
                    Subscription.status == "pending",
                )
            )
            if existing:
                return existing, False
            subscription = Subscription(
                user_id=user.id,
                plan_id=plan.id,
                status="pending",
                request_key=request_key,
            )
            session.add(subscription)
            await session.flush()
            return subscription, True

    async def pending_subscriptions(self, limit: int = 20) -> list[tuple[Subscription, User, Plan]]:
        async with self.sessions() as session:
            result = await session.execute(
                select(Subscription, User, Plan)
                .join(User, User.id == Subscription.user_id)
                .join(Plan, Plan.id == Subscription.plan_id)
                .where(Subscription.status == "pending")
                .order_by(Subscription.requested_at)
                .limit(limit)
            )
            return list(result.all())

    async def review_subscription(self, subscription_id: int, admin_id: int, approve: bool) -> None:
        async with self.sessions.begin() as session:
            subscription = await session.get(Subscription, subscription_id, with_for_update=True)
            if subscription is None:
                raise NotFound()
            if subscription.status != "pending":
                return
            user = await session.get(User, subscription.user_id, with_for_update=True)
            plan = await session.get(Plan, subscription.plan_id)
            if user is None or plan is None:
                raise NotFound()
            now = datetime.now(UTC)
            if approve:
                subscription.status = "active"
                subscription.starts_at = now
                subscription.ends_at = now + timedelta(days=plan.duration_days)
                user.plan_id = plan.id
                action = "subscription_approved"
            else:
                subscription.status = "rejected"
                action = "subscription_rejected"
            subscription.reviewed_by = admin_id
            session.add(
                AuditLog(
                    actor_telegram_id=admin_id,
                    action=action,
                    subject=f"subscription:{subscription_id}",
                    details={"user_id": user.telegram_id, "plan": plan.code},
                )
            )

    async def update_plan(
        self,
        plan_code: str,
        *,
        admin_id: int,
        enabled: bool | None = None,
        usage_quota: int | None = None,
        price_minor: int | None = None,
    ) -> None:
        async with self.sessions.begin() as session:
            plan = await session.scalar(
                select(Plan).where(Plan.code == plan_code).with_for_update()
            )
            if plan is None:
                raise NotFound()
            changes: dict[str, Any] = {}
            if enabled is not None:
                if plan.code == "free" and not enabled:
                    raise ValueError("The Free plan cannot be disabled.")
                plan.enabled = enabled
                changes["enabled"] = enabled
            if usage_quota is not None:
                if not 0 <= usage_quota <= 100_000:
                    raise ValueError("Daily plan quota must be from 0 to 100000.")
                plan.usage_quota = usage_quota
                changes["usage_quota"] = usage_quota
            if price_minor is not None:
                if price_minor < 0 or price_minor > 100_000_000:
                    raise ValueError("Price must be between 0 and 1000000.00.")
                plan.price_minor = price_minor
                changes["price_minor"] = price_minor
            session.add(
                AuditLog(
                    actor_telegram_id=admin_id,
                    action="plan_updated",
                    subject=f"plan:{plan_code}",
                    details=changes,
                )
            )

    async def request_summary(self, subscription_id: int) -> tuple[Subscription, User, Plan] | None:
        async with self.sessions() as session:
            result = await session.execute(
                select(Subscription, User, Plan)
                .join(User, User.id == Subscription.user_id)
                .join(Plan, Plan.id == Subscription.plan_id)
                .where(Subscription.id == subscription_id)
            )
            return result.one_or_none()

    async def count_pending(self) -> int:
        async with self.sessions() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(Subscription)
                    .where(Subscription.status == "pending")
                )
                or 0
            )
