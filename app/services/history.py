from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.models import SandboxTransaction, SimulationResult, User


class HistoryService:
    def __init__(self, sessions: async_sessionmaker) -> None:
        self.sessions = sessions

    async def recent(
        self, telegram_id: int, limit: int = 10
    ) -> tuple[list[SimulationResult], list[SandboxTransaction]]:
        async with self.sessions() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                return [], []
            simulations = list(
                (
                    await session.scalars(
                        select(SimulationResult)
                        .where(SimulationResult.user_id == user.id)
                        .order_by(SimulationResult.created_at.desc())
                        .limit(limit)
                    )
                ).all()
            )
            sandbox = list(
                (
                    await session.scalars(
                        select(SandboxTransaction)
                        .where(SandboxTransaction.user_id == user.id)
                        .order_by(SandboxTransaction.created_at.desc())
                        .limit(limit)
                    )
                ).all()
            )
            return simulations, sandbox
