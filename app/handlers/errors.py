from __future__ import annotations

import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.handlers.common import reply_or_edit
from app.ui import error_message

logger = logging.getLogger(__name__)


async def centralized_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    if error is None:
        logger.error("Unhandled Telegram update without an exception detail")
        return
    logger.error("Unhandled Telegram update (%s)", type(error).__name__)
    if not isinstance(update, Update):
        return
    try:
        await reply_or_edit(
            update,
            error_message(
                "Service unavailable",
                "The request could not be completed. No unverified result was reported.",
            ),
        )
    except TelegramError:
        logger.info("Could not send the safe error response to Telegram")
