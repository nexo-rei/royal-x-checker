from __future__ import annotations

import logging
import sys

from pydantic import ValidationError
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, Defaults

from app.config import Settings, get_settings
from app.handlers import register_handlers
from app.handlers.errors import centralized_error_handler
from app.logging_config import configure_logging
from app.runtime import Runtime

logger = logging.getLogger(__name__)


def build_application(settings: Settings | None = None) -> Application:
    config = settings or get_settings()
    config.validate_runtime()
    runtime = Runtime.build(config)

    async def post_init(application: Application) -> None:
        application.bot_data["runtime"] = runtime
        await runtime.startup()
        logger.info("Royal X Checker polling bot initialized")

    async def post_shutdown(_application: Application) -> None:
        await runtime.close()
        logger.info("Royal X Checker shut down cleanly")

    application = (
        Application.builder()
        .token(config.bot_token.get_secret_value())
        .defaults(Defaults(parse_mode=ParseMode.HTML, allow_sending_without_reply=True))
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    register_handlers(application)
    application.add_error_handler(centralized_error_handler)
    return application


def main() -> None:
    try:
        settings = get_settings()
        settings.validate_runtime()
    except ValidationError:
        print(
            "Configuration error: one or more settings are invalid; secret values are omitted.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    except Exception as exc:
        print(
            f"Configuration error ({type(exc).__name__}); check required environment variables.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    configure_logging(settings.log_level)
    application = build_application(settings)
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)


if __name__ == "__main__":
    main()
