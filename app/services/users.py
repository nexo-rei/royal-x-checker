from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.models import AuditLog, Plan, UsageRecord, User, utcnow
from app.services.plans import PlanService


@dataclass(frozen=True, slots=True)
class UserProfile:
    telegram_id: int
    display_name: str
    plan_name: str
    plan_code: str
    credits: int
    usage_today: int
    daily_limit: int
    member_since: datetime
    status: str


class UserService:
    def __init__(self, sessions: async_sessionmaker, free_initial_credits: int = 5) -> None:
        self.sessions = sessions
        self.free_initial_credits = free_initial_credits

    async def ensure_registered(self, telegram_id: int, display_name: str) -> User:
        safe_name = (display_name.strip() or "Telegram user")[:256]
        try:
            async with self.sessions.begin() as session:
                user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
                if user:
                    user.display_name = safe_name
                    return user
                free_plan = await session.scalar(select(Plan).where(Plan.code == "free"))
                if free_plan is None:
                    raise RuntimeError("Default plans must be seeded before user registration")
                user = User(
                    telegram_id=telegram_id,
                    display_name=safe_name,
                    plan_id=free_plan.id,
                    credits=self.free_initial_credits,
                    status="active",
                )
                session.add(user)
                await session.flush()
                if self.free_initial_credits:
                    from app.database.models import CreditLedger

                    session.add(
                        CreditLedger(
                            user_id=user.id,
                            delta=self.free_initial_credits,
                            balance_after=self.free_initial_credits,
                            reason="initial_free_credits",
                            idempotency_key=f"welcome:{telegram_id}",
                        )
                    )
                return user
        except IntegrityError:
            async with self.sessions.begin() as session:
                user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
                if user is None:
                    raise
                user.display_name = safe_name
                return user

    async def profile(self, telegram_id: int) -> UserProfile | None:
        now = utcnow()
        day_start = datetime.combine(now.date(), time.min, tzinfo=UTC)
        async with self.sessions() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                return None
            plan = await PlanService.effective_plan(session, user, now)
            usage_today = int(
                await session.scalar(
                    select(func.count(UsageRecord.id)).where(
                        UsageRecord.user_id == user.id,
                        UsageRecord.created_at >= day_start,
                        UsageRecord.status.in_(("processing", "completed", "indeterminate")),
                    )
                )
                or 0
            )
            return UserProfile(
                telegram_id=user.telegram_id,
                display_name=user.display_name,
                plan_name=plan.name,
                plan_code=plan.code,
                credits=user.credits,
                usage_today=usage_today,
                daily_limit=plan.usage_quota,
                member_since=user.registered_at,
                status=user.status,
            )

    async def by_telegram_id(self, telegram_id: int) -> User | None:
        async with self.sessions() as session:
            return await session.scalar(select(User).where(User.telegram_id == telegram_id))

    async def set_status(self, telegram_id: int, status: str, admin_id: int) -> bool:
        if status not in {"active", "suspended"}:
            raise ValueError("Unsupported account status.")
        async with self.sessions.begin() as session:
            user = await session.scalar(
                select(User).where(User.telegram_id == telegram_id).with_for_update()
            )
            if user is None:
                return False
            user.status = status
            session.add(
                AuditLog(
                    actor_telegram_id=admin_id,
                    action=f"user_{status}",
                    subject=f"telegram:{telegram_id}",
                    details={},
                )
            )
            return True

    async def record_audit(
        self,
        actor_id: int,
        action: str,
        *,
        subject: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        async with self.sessions.begin() as session:
            session.add(
                AuditLog(
                    actor_telegram_id=actor_id,
                    action=action,
                    subject=subject,
                    details=details or {},
                )
            )
