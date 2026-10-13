from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.handlers.common import (
    acknowledge,
    check_active,
    clear_pending,
    get_runtime,
    reply_or_edit,
)
from app.keyboards.menus import back_menu
from app.ui import BRAND, SEPARATOR, error_message


async def begin_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = get_runtime(context)
    if not await check_active(update, runtime):
        return
    if not await runtime.features.is_enabled("feedback"):
        await reply_or_edit(
            update, error_message("Feedback disabled", "Feedback is temporarily disabled.")
        )
        return
    context.user_data["pending_action"] = "feedback"
    await reply_or_edit(
        update,
        f"{BRAND}\n<b>📩 FEEDBACK</b>\n{SEPARATOR}\n"
        f"Send one message with your feedback (up to {runtime.settings.max_feedback_chars} characters).\n"
        "Do not include card numbers, CVVs, API keys, or other payment credentials.",
        back_menu("nav:main"),
    )


async def feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await acknowledge(update)
    data = update.callback_query.data if update.callback_query else ""
    if data == "feedback:cancel":
        clear_pending(context)
        await reply_or_edit(update, f"{BRAND}\nFeedback cancelled.", back_menu("nav:main"))
