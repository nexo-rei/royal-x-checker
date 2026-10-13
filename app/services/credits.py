from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.models import (
    ApplicationSetting,
    CreditLedger,
    SandboxTransaction,
    UsageRecord,
    User,
)
from app.services.errors import (
    FeatureDisabled,
    InsufficientCredits,
    NotFound,
    QuotaExceeded,
    UserSuspended,
)
from app.services.plans import PlanService


@dataclass(frozen=True, slots=True)
class OperationReservation:
    usage_id: int
    idempotency_key: str
    status: str
    balance_after: int
    duplicate: bool


@dataclass(frozen=True, slots=True)
class PendingOperation:
    usage_id: int
    idempotency_key: str
    details: dict[str, Any]
    status: str


class CreditService:
    def __init__(self, sessions: async_sessionmaker, default_costs: dict[str, int]) -> None:
        self.sessions = sessions
        self.default_costs = default_costs

    async def feature_cost(self, feature: str) -> int:
        async with self.sessions() as session:
            setting = await session.scalar(
                select(ApplicationSetting).where(
                    ApplicationSetting.key == f"feature_cost:{feature}"
                )
            )
            value = setting.value if setting is not None else self.default_costs.get(feature)
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 10_000:
                raise RuntimeError(f"Invalid configured feature cost for {feature}")
            return value

    async def reserve_operation(
        self,
        *,
        telegram_id: int,
        feature: str,
        idempotency_key: str,
        cost: int,
    ) -> OperationReservation:
        if not 0 <= cost <= 10_000:
            raise ValueError("Configured operation cost is outside the supported range.")
        try:
            async with self.sessions.begin() as session:
                existing = await session.scalar(
                    select(UsageRecord).where(UsageRecord.idempotency_key == idempotency_key)
                )
                if existing is not None:
                    user = await session.get(User, existing.user_id)
                    return OperationReservation(
                        existing.id,
                        existing.idempotency_key,
                        existing.status,
                        user.credits if user else 0,
                        True,
                    )

                user_id_result = await session.execute(
                    update(User)
                    .where(
                        User.telegram_id == telegram_id,
                        User.status == "active",
                        User.credits >= cost,
                    )
                    .values(credits=User.credits - cost)
                    .returning(User.id, User.credits)
                )
                updated = user_id_result.first()
                if updated is None:
                    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
                    if user is None:
                        raise NotFound()
                    if user.status != "active":
                        raise UserSuspended()
                    raise InsufficientCredits()

                user_id, balance_after = updated
                user = await session.get(User, user_id)
                if user is None:
                    raise NotFound()
                plan = await PlanService.effective_plan(session, user)
                if feature not in (plan.allowed_features or []):
                    raise FeatureDisabled()

                now = datetime.now(UTC)
                day_start = datetime.combine(now.date(), time.min, tzinfo=UTC)
                usage_today = int(
                    await session.scalar(
                        select(func.count(UsageRecord.id)).where(
                            UsageRecord.user_id == user_id,
                            UsageRecord.created_at >= day_start,
                            UsageRecord.status.in_(("processing", "completed", "indeterminate")),
                        )
                    )
                    or 0
                )
                if usage_today >= plan.usage_quota:
                    raise QuotaExceeded()

                record = UsageRecord(
                    user_id=user_id,
                    feature=feature,
                    status="processing",
                    credits_charged=cost,
                    idempotency_key=idempotency_key,
                    details={},
                )
                session.add(record)
                await session.flush()
                if cost:
                    session.add(
                        CreditLedger(
                            user_id=user_id,
                            delta=-cost,
                            balance_after=balance_after,
                            reason=f"operation:{feature}",
                            idempotency_key=self._ledger_key(idempotency_key, "debit"),
                        )
                    )
                return OperationReservation(
                    record.id,
                    idempotency_key,
                    record.status,
                    int(balance_after),
                    False,
                )
        except IntegrityError:
            async with self.sessions() as session:
                existing = await session.scalar(
                    select(UsageRecord).where(UsageRecord.idempotency_key == idempotency_key)
                )
                if existing is None:
                    raise
                user = await session.get(User, existing.user_id)
                return OperationReservation(
                    existing.id,
                    existing.idempotency_key,
                    existing.status,
                    user.credits if user else 0,
                    True,
                )

    async def complete_operation(
        self, usage_id: int, details: dict[str, Any] | None = None
    ) -> None:
        async with self.sessions.begin() as session:
            record = await session.get(UsageRecord, usage_id, with_for_update=True)
            if record is None:
                raise NotFound()
            if record.status == "completed":
                return
            if record.status not in {"processing", "indeterminate"}:
                return
            record.status = "completed"
            record.completed_at = datetime.now(UTC)
            if details:
                record.details = {**(record.details or {}), **details}

    async def mark_indeterminate(
        self, usage_id: int, details: dict[str, Any] | None = None
    ) -> None:
        async with self.sessions.begin() as session:
            record = await session.get(UsageRecord, usage_id, with_for_update=True)
            if record and record.status in {"processing", "indeterminate"}:
                record.status = "indeterminate"
                record.details = {
                    **(record.details or {}),
                    **(details or {}),
                    "integration_state": "awaiting_idempotent_retry",
                }

    async def resume_indeterminate(
        self, *, telegram_id: int, usage_id: int, feature: str
    ) -> PendingOperation:
        async with self.sessions.begin() as session:
            record = await session.get(UsageRecord, usage_id, with_for_update=True)
            if record is None or record.feature != feature:
                raise NotFound()
            user = await session.get(User, record.user_id)
            if user is None or user.telegram_id != telegram_id:
                raise NotFound()
            if user.status != "active":
                raise UserSuspended()
            if record.status != "indeterminate":
                raise ValueError("This operation is not waiting for a safe retry.")
            record.status = "processing"
            return PendingOperation(
                record.id, record.idempotency_key, dict(record.details or {}), record.status
            )

    async def fail_operation(self, usage_id: int, *, refund: bool = True, reason_code: str) -> None:
        async with self.sessions.begin() as session:
            record = await session.get(UsageRecord, usage_id, with_for_update=True)
            if record is None or record.status in {"completed", "failed"}:
                return
            user = await session.get(User, record.user_id, with_for_update=True)
            if user is None:
                record.status = "failed"
                return
            if refund and record.credits_charged:
                updated = await session.execute(
                    update(User)
                    .where(User.id == user.id)
                    .values(credits=User.credits + record.credits_charged)
                    .returning(User.credits)
                )
                balance_after = int(updated.scalar_one())
                session.add(
                    CreditLedger(
                        user_id=user.id,
                        delta=record.credits_charged,
                        balance_after=balance_after,
                        reason=f"refund:{record.feature}",
                        idempotency_key=self._ledger_key(record.idempotency_key, "refund"),
                    )
                )
            record.status = "failed"
            record.completed_at = datetime.now(UTC)
            record.details = {**(record.details or {}), "failure_code": reason_code[:80]}

    async def get_pending_operation(
        self, telegram_id: int, usage_id: int, feature: str
    ) -> PendingOperation | None:
        async with self.sessions() as session:
            record = await session.scalar(
                select(UsageRecord)
                .join(User, User.id == UsageRecord.user_id)
                .where(
                    UsageRecord.id == usage_id,
                    UsageRecord.feature == feature,
                    User.telegram_id == telegram_id,
                )
            )
            if record is None:
                return None
            return PendingOperation(
                record.id, record.idempotency_key, dict(record.details or {}), record.status
            )

    async def grant_credits(
        self,
        *,
        target_telegram_id: int,
        amount: int,
        admin_id: int,
        idempotency_key: str,
    ) -> int:
        if not 1 <= amount <= 100_000:
            raise ValueError("Credit grant must be between 1 and 100000.")
        try:
            async with self.sessions.begin() as session:
                user = await session.scalar(
                    select(User).where(User.telegram_id == target_telegram_id).with_for_update()
                )
                if user is None:
                    raise NotFound()
                updated = await session.execute(
                    update(User)
                    .where(User.id == user.id)
                    .values(credits=User.credits + amount)
                    .returning(User.credits)
                )
                balance = int(updated.scalar_one())
                session.add(
                    CreditLedger(
                        user_id=user.id,
                        delta=amount,
                        balance_after=balance,
                        reason="admin_approved_credit_grant",
                        idempotency_key=self._ledger_key(idempotency_key, "admin-credit"),
                        actor_telegram_id=admin_id,
                    )
                )
                return balance
        except IntegrityError:
            async with self.sessions() as session:
                user = await session.scalar(
                    select(User).where(User.telegram_id == target_telegram_id)
                )
                if user is None:
                    raise NotFound() from None
                return user.credits

    async def recover_stale_operations(self, older_than_seconds: int = 300) -> int:
        cutoff_dt = datetime.fromtimestamp(
            datetime.now(UTC).timestamp() - older_than_seconds,
            UTC,
        )
        recovered_count = 0
        async with self.sessions.begin() as session:
            stale = await session.scalars(
                select(UsageRecord)
                .where(UsageRecord.status == "processing", UsageRecord.created_at < cutoff_dt)
                .with_for_update()
            )
            for record in stale:
                recovered_count += 1
                if record.feature == "stripe_sandbox":
                    record.status = "indeterminate"
                    record.details = {
                        **(record.details or {}),
                        "integration_state": "awaiting_idempotent_retry",
                    }
                    continue
                user = await session.get(User, record.user_id, with_for_update=True)
                if user is not None and record.credits_charged:
                    updated = await session.execute(
                        update(User)
                        .where(User.id == user.id)
                        .values(credits=User.credits + record.credits_charged)
                        .returning(User.credits)
                    )
                    balance_after = int(updated.scalar_one())
                    session.add(
                        CreditLedger(
                            user_id=user.id,
                            delta=record.credits_charged,
                            balance_after=balance_after,
                            reason=f"restart_refund:{record.feature}",
                            idempotency_key=self._ledger_key(
                                record.idempotency_key, "restart-refund"
                            ),
                        )
                    )
                record.status = "failed"
                record.completed_at = datetime.now(UTC)
                record.details = {**(record.details or {}), "failure_code": "process_interruption"}

            pending_ids = select(UsageRecord.id).where(
                UsageRecord.feature == "stripe_sandbox",
                UsageRecord.status == "indeterminate",
            )
            await session.execute(
                update(SandboxTransaction)
                .where(
                    SandboxTransaction.usage_id.in_(pending_ids),
                    SandboxTransaction.status == "processing",
                )
                .values(
                    status="indeterminate",
                    outcome="integration_error",
                    safe_metadata={"reason": "process_interruption", "test_mode": True},
                )
            )
        return recovered_count

    @staticmethod
    def _ledger_key(idempotency_key: str, suffix: str) -> str:
        return f"{suffix}:{hashlib.sha256(idempotency_key.encode('utf-8')).hexdigest()}"
