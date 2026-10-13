from __future__ import annotations

import logging
from typing import Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from app.runtime import Runtime
from app.services.errors import ServiceError, UserSuspended
from app.ui import error_message

logger = logging.getLogger(__name__)


def get_runtime(context: ContextTypes.DEFAULT_TYPE) -> Runtime:
    runtime = context.application.bot_data.get("runtime")
    if not isinstance(runtime, Runtime):
        raise RuntimeError("Application runtime was not initialized")
    return runtime


def telegram_user_id(update: Update) -> int | None:
    user = update.effective_user
    return user.id if user else None


def is_admin(runtime: Runtime, telegram_id: int | None) -> bool:
    return telegram_id is not None and telegram_id in runtime.settings.owner_ids


def clear_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in (
        "pending_action",
        "simulation_scenario",
        "admin_target_id",
        "admin_plan_code",
        "admin_feature_code",
        "pending_credit_amount",
        "pending_plan_quota",
        "pending_plan_price",
        "pending_feature_cost",
        "admin_feature_confirm",
    ):
        context.user_data.pop(key, None)


async def ensure_registered(update: Update, runtime: Runtime):
    telegram_user = update.effective_user
    if telegram_user is None:
        return None
    name = telegram_user.full_name or telegram_user.first_name or "Telegram user"
    return await runtime.users.ensure_registered(telegram_user.id, name)


async def check_active(update: Update, runtime: Runtime) -> bool:
    telegram_id = telegram_user_id(update)
    if telegram_id is None:
        return False
    await ensure_registered(update, runtime)
    profile = await runtime.users.profile(telegram_id)
    if profile is None:
        return False
    if profile.status != "active" and not is_admin(runtime, telegram_id):
        await reply_or_edit(
            update, error_message("Account suspended", UserSuspended.public_message)
        )
        return False
    return True


async def rate_allowed(update: Update, runtime: Runtime) -> bool:
    telegram_id = telegram_user_id(update)
    if telegram_id is None:
        return False
    allowed, retry_after = await runtime.rate_limiter.allow(f"user:{telegram_id}")
    if not allowed:
        await reply_or_edit(
            update,
            error_message(
                "Rate limit", f"Wait about {max(1, int(retry_after))} seconds before trying again."
            ),
        )
        return False
    return True


async def acknowledge(update: Update) -> None:
    query = update.callback_query
    if query is not None:
        try:
            await query.answer()
        except TelegramError:
            logger.info("Telegram callback acknowledgement failed")


async def reply_or_edit(
    update: Update,
    text: str,
    reply_markup: Any = None,
    *,
    parse_mode: str | ParseMode = ParseMode.HTML,
) -> None:
    query = update.callback_query
    if query is not None and query.message is not None:
        try:
            await query.edit_message_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )
            return
        except BadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
            logger.info("Telegram edit unavailable; sending a new response")
        except TelegramError:
            logger.info("Telegram edit failed; sending a new response")
        try:
            await query.message.reply_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )
            return
        except TelegramError:
            logger.info("Telegram fallback response failed")
            return
    message = update.effective_message
    if message is not None:
        try:
            await message.reply_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )
        except TelegramError:
            logger.info("Telegram response failed")


async def rate_limit_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    runtime = get_runtime(context)
    return await rate_allowed(update, runtime)


async def handle_service_error(update: Update, error: Exception) -> None:
    if isinstance(error, ServiceError):
        await reply_or_edit(update, error_message("Request not completed", error.public_message))
    elif isinstance(error, ValueError):
        await reply_or_edit(update, error_message("Invalid input", str(error)))
    else:
        logger.error("Request failed safely (%s)", type(error).__name__)
        await reply_or_edit(
            update,
            error_message(
                "Service unavailable",
                "The request could not be completed. No unverified result was reported.",
            ),
        )
