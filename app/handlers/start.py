from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.handlers.common import clear_pending, get_runtime, is_admin
from app.keyboards.menus import main_menu
from app.ui import welcome_message


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = get_runtime(context)
    telegram_user = update.effective_user
    message = update.effective_message
    if telegram_user is None or message is None:
        return
    display_name = telegram_user.full_name or telegram_user.first_name or "Telegram user"
    await runtime.users.ensure_registered(telegram_user.id, display_name)
    clear_pending(context)
    profile = await runtime.users.profile(telegram_user.id)
    await message.reply_text(
        welcome_message(
            telegram_user.first_name or "there",
            profile,
            is_admin(runtime, telegram_user.id),
        ),
        reply_markup=main_menu(is_admin(runtime, telegram_user.id)),
        disable_web_page_preview=True,
    )
