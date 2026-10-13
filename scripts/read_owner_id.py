from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from telegram import Bot

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402


async def main() -> int:
    settings = get_settings()
    if settings.bot_token is None or not settings.bot_token.get_secret_value().strip():
        print("Set BOT_TOKEN in the host environment or local .env first.", file=sys.stderr)
        return 2

    async with Bot(token=settings.bot_token.get_secret_value()) as bot:
        updates = await bot.get_updates(timeout=0, limit=100, allowed_updates=["message"])

    matching_ids: set[int] = set()
    for update in updates:
        user = update.effective_user
        message = update.effective_message
        parts = message.text.strip().split(maxsplit=1) if message and message.text else []
        if user and parts and parts[0].split("@")[0] == "/whoami":
            matching_ids.add(user.id)
    if not matching_ids:
        print("No /whoami update found. Send /whoami to this bot, then run this command again.")
        return 1
    print("Numeric Telegram ID(s) for pending /whoami request(s):")
    for telegram_id in sorted(matching_ids):
        print(telegram_id)
    print("The bot token and message contents were not printed. Add only your own ID to OWNER_IDS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
