from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.database.models import ApplicationSetting, AuditLog


class FeatureService:
    def __init__(self, sessions: async_sessionmaker, settings: Settings) -> None:
        self.sessions = sessions
        self.settings = settings
        self.defaults = {
            "stripe_sandbox": True,
            "bin_lookup": settings.bin_lookup_enabled,
            "file_analysis": True,
            "simulation": True,
            "synthetic_data": True,
            "fraud_analysis": True,
            "feedback": True,
        }

    async def is_enabled(self, feature: str) -> bool:
        if feature not in self.defaults:
            return False
        async with self.sessions() as session:
            setting = await session.scalar(
                select(ApplicationSetting).where(
                    ApplicationSetting.key == f"feature_enabled:{feature}"
                )
            )
            return bool(setting.value) if setting is not None else self.defaults[feature]

    async def states(self) -> dict[str, bool]:
        async with self.sessions() as session:
            settings = await session.scalars(
                select(ApplicationSetting).where(ApplicationSetting.key.like("feature_enabled:%"))
            )
            values = {
                item.key.removeprefix("feature_enabled:"): bool(item.value) for item in settings
            }
        return {name: values.get(name, default) for name, default in self.defaults.items()}

    async def set_enabled(self, feature: str, enabled: bool, admin_id: int) -> None:
        if feature not in self.defaults:
            raise ValueError("Unknown feature.")
        async with self.sessions.begin() as session:
            key = f"feature_enabled:{feature}"
            setting = await session.scalar(
                select(ApplicationSetting).where(ApplicationSetting.key == key).with_for_update()
            )
            if setting is None:
                setting = ApplicationSetting(key=key, value=enabled, updated_by=admin_id)
                session.add(setting)
            else:
                setting.value = enabled
                setting.updated_by = admin_id
            session.add(
                AuditLog(
                    actor_telegram_id=admin_id,
                    action="feature_toggled",
                    subject=feature,
                    details={"enabled": enabled},
                )
            )

    async def set_cost(self, feature: str, cost: int, admin_id: int) -> None:
        if feature not in self.settings.feature_costs or not 0 <= cost <= 10_000:
            raise ValueError("Select a supported feature and a cost from 0 to 10000 credits.")
        async with self.sessions.begin() as session:
            key = f"feature_cost:{feature}"
            setting = await session.scalar(
                select(ApplicationSetting).where(ApplicationSetting.key == key).with_for_update()
            )
            if setting is None:
                setting = ApplicationSetting(key=key, value=cost, updated_by=admin_id)
                session.add(setting)
            else:
                setting.value = cost
                setting.updated_by = admin_id
            session.add(
                AuditLog(
                    actor_telegram_id=admin_id,
                    action="feature_cost_changed",
                    subject=feature,
                    details={"cost": cost},
                )
            )
