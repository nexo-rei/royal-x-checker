from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.database.models import Base


class Database:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        engine_options: dict[str, Any] = {"echo": False, "pool_pre_ping": True}
        if (
            database_url.startswith(("sqlite+aiosqlite:///:memory:", "sqlite+aiosqlite://"))
            and ":memory:" in database_url
        ):
            engine_options["poolclass"] = StaticPool
        self.engine: AsyncEngine = create_async_engine(database_url, **engine_options)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        if database_url.startswith("sqlite+"):
            event.listen(self.engine.sync_engine, "connect", self._configure_sqlite)
            self._ensure_sqlite_directory(database_url)

    @staticmethod
    def _configure_sqlite(connection: Any, _record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    @staticmethod
    def _ensure_sqlite_directory(database_url: str) -> None:
        parsed = urlparse(database_url.replace("sqlite+aiosqlite", "sqlite", 1))
        if parsed.path and parsed.path not in ("/:memory:", "/"):
            Path(parsed.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    async def migrate(self) -> None:
        """Run checked-in Alembic migrations against the configured async database."""
        config_path = Path(__file__).resolve().parents[2] / "alembic.ini"
        if not config_path.is_file():
            raise RuntimeError("Alembic configuration was not found")
        config = Config(str(config_path))
        config.set_main_option("sqlalchemy.url", self._sync_database_url().replace("%", "%%"))

        def upgrade(sync_connection: Any) -> None:
            config.attributes["connection"] = sync_connection
            command.upgrade(config, "head")

        async with self.engine.begin() as connection:
            await connection.run_sync(upgrade)

    def _sync_database_url(self) -> str:
        return self.database_url.replace("+aiosqlite", "").replace("+asyncpg", "")

    async def create_schema_for_tests(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()
