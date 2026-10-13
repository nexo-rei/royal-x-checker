from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.models import AuditLog, SandboxTransaction, SimulationResult, UsageRecord, User


@dataclass(frozen=True, slots=True)
class AdminStatistics:
    total_users: int
    active_users: int
    operations_today: int
    simulation_count: int
    sandbox_count: int
    pending_sandbox: int


class AdminService:
    def __init__(self, sessions: async_sessionmaker) -> None:
        self.sessions = sessions

    async def statistics(self) -> AdminStatistics:
        now = datetime.now(UTC)
        day_start = datetime.combine(now.date(), time.min, tzinfo=UTC)
        async with self.sessions() as session:
            total = int(await session.scalar(select(func.count(User.id))) or 0)
            active = int(
                await session.scalar(select(func.count(User.id)).where(User.status == "active"))
                or 0
            )
            today = int(
                await session.scalar(
                    select(func.count(UsageRecord.id)).where(
                        UsageRecord.created_at >= day_start,
                        UsageRecord.status.in_(("processing", "completed", "indeterminate")),
                    )
                )
                or 0
            )
            simulations = int(await session.scalar(select(func.count(SimulationResult.id))) or 0)
            sandbox_total = int(
                await session.scalar(select(func.count(SandboxTransaction.id))) or 0
            )
            sandbox_pending = int(
                await session.scalar(
                    select(func.count(SandboxTransaction.id)).where(
                        SandboxTransaction.status.not_in(("succeeded", "canceled"))
                    )
                )
                or 0
            )
            return AdminStatistics(
                total, active, today, simulations, sandbox_total, sandbox_pending
            )

    async def recent_audit(self, limit: int = 10) -> list[AuditLog]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
            )
            return list(rows.all())

    async def recent_sandbox(self, limit: int = 10) -> list[SandboxTransaction]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(SandboxTransaction)
                .order_by(SandboxTransaction.created_at.desc())
                .limit(limit)
            )
            return list(rows.all())
