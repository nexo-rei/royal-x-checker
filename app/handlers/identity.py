from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes


async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    await message.reply_text(
        f"Your numeric Telegram ID is <code>{user.id}</code>. Add only your own ID to OWNER_IDS.",
        parse_mode="HTML",
    )
