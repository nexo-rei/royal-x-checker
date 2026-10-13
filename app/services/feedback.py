from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.models import AuditLog, Feedback, User
from app.services.errors import InvalidInput, NotFound
from app.services.file_tools import contains_payment_credentials


class FeedbackService:
    def __init__(self, sessions: async_sessionmaker, max_chars: int = 2_000) -> None:
        self.sessions = sessions
        self.max_chars = max_chars

    async def submit(self, telegram_id: int, text: str) -> int:
        message = text.strip()
        if not message or len(message) > self.max_chars:
            raise InvalidInput(f"Feedback must contain 1 to {self.max_chars} characters.")
        if contains_payment_credentials(message):
            raise InvalidInput(
                "Feedback containing apparent card numbers or security codes is rejected."
            )
        async with self.sessions.begin() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                raise NotFound()
            feedback = Feedback(user_id=user.id, message=message, status="new")
            session.add(feedback)
            await session.flush()
            return feedback.id

    async def open_feedback(self, limit: int = 20) -> list[tuple[Feedback, User]]:
        async with self.sessions() as session:
            result = await session.execute(
                select(Feedback, User)
                .join(User, User.id == Feedback.user_id)
                .where(Feedback.status == "new")
                .order_by(Feedback.created_at)
                .limit(limit)
            )
            return list(result.all())

    async def get(self, feedback_id: int) -> tuple[Feedback, User] | None:
        async with self.sessions() as session:
            result = await session.execute(
                select(Feedback, User)
                .join(User, User.id == Feedback.user_id)
                .where(Feedback.id == feedback_id)
            )
            return result.one_or_none()

    async def resolve(self, feedback_id: int, admin_id: int) -> None:
        async with self.sessions.begin() as session:
            feedback = await session.get(Feedback, feedback_id, with_for_update=True)
            if feedback is None:
                raise NotFound()
            if feedback.status != "reviewed":
                feedback.status = "reviewed"
                feedback.reviewed_by = admin_id
                feedback.reviewed_at = datetime.now(UTC)
                session.add(
                    AuditLog(
                        actor_telegram_id=admin_id,
                        action="feedback_reviewed",
                        subject=f"feedback:{feedback_id}",
                        details={},
                    )
                )
