from __future__ import annotations

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from app.handlers.admin import admin_callback, admin_command
from app.handlers.feedback import feedback_callback
from app.handlers.gates import gates_callback, gates_command
from app.handlers.identity import whoami_command
from app.handlers.input import text_input_handler
from app.handlers.menu import help_command, menu_callback, menu_command, profile_command
from app.handlers.plans import plans_callback, plans_command
from app.handlers.start import start_command
from app.handlers.tools import file_upload_handler, tools_callback, tools_command


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("gates", gates_command))
    application.add_handler(CommandHandler("tools", tools_command))
    application.add_handler(CommandHandler("plans", plans_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("whoami", whoami_command))
    application.add_handler(CommandHandler("profile", profile_command))
    application.add_handler(CommandHandler("admin", admin_command))

    application.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^nav:"))
    application.add_handler(CallbackQueryHandler(gates_callback, pattern=r"^(?:gate:|sim:|auth:)"))
    application.add_handler(CallbackQueryHandler(tools_callback, pattern=r"^tool:"))
    application.add_handler(CallbackQueryHandler(plans_callback, pattern=r"^plan:"))
    application.add_handler(CallbackQueryHandler(feedback_callback, pattern=r"^feedback:"))
    application.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^admin:"))
    application.add_handler(MessageHandler(filters.Document.ALL, file_upload_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input_handler))
