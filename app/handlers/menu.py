from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.handlers.common import (
    acknowledge,
    check_active,
    clear_pending,
    ensure_registered,
    get_runtime,
    is_admin,
    reply_or_edit,
)
from app.keyboards.menus import gates_menu, main_menu, profile_menu, tools_menu
from app.ui import BRAND, SEPARATOR, error_message, profile_message, welcome_message

_HELP_TEXT = (
    f"{BRAND}\n<b>ℹ️ HELP & LIMITS</b>\n{SEPARATOR}\n"
    "🧪 <b>AUTH</b> uses only a configured Stripe test-mode integration and its documented test PaymentMethod. "
    "No card details are collected.\n"
    "💳 <b>CHARGE</b> is a local simulator. Its outcomes are synthetic and never payment authorizations.\n"
    "🔎 <b>BIN lookup</b> returns public metadata for a 6- or 8-digit prefix only; it never checks card activity.\n"
    "🧬 Synthetic profiles and transactions are fictional. File analysis accepts labelled synthetic CSV only.\n\n"
    "Commands: /start · /menu · /gates · /tools · /plans · /profile · /help · /whoami\n"
    "Use /admin only if you are an authorized owner. Premium activation is reviewed by an administrator."
)


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = get_runtime(context)
    telegram_user = update.effective_user
    if telegram_user is None or update.effective_message is None:
        return
    await ensure_registered(update, runtime)
    clear_pending(context)
    profile = await runtime.users.profile(telegram_user.id)
    if context.args and context.args[0].lower() == "profile":
        if profile:
            await update.effective_message.reply_text(
                profile_message(profile), reply_markup=profile_menu()
            )
        return
    await update.effective_message.reply_text(
        welcome_message(
            telegram_user.first_name or "there", profile, is_admin(runtime, telegram_user.id)
        ),
        reply_markup=main_menu(is_admin(runtime, telegram_user.id)),
    )


async def gates_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = get_runtime(context)
    if not await check_active(update, runtime):
        return
    clear_pending(context)
    await reply_or_edit(
        update,
        f"{BRAND}\n<b>👑 GATES</b>\n{SEPARATOR}\nChoose a controlled testing area.",
        gates_menu(),
    )


async def tools_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = get_runtime(context)
    if not await check_active(update, runtime):
        return
    clear_pending(context)
    enabled = {
        name
        for name in ("bin_lookup", "synthetic_data", "file_analysis", "fraud_analysis")
        if await runtime.features.is_enabled(name)
    }
    await reply_or_edit(
        update,
        f"{BRAND}\n<b>🛠️ TOOLS</b>\n{SEPARATOR}\nOnly active, implemented tools are shown.",
        tools_menu(enabled),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = get_runtime(context)
    if update.effective_message:
        await ensure_registered(update, runtime)
        clear_pending(context)
        await update.effective_message.reply_text(
            _HELP_TEXT,
            reply_markup=main_menu(
                is_admin(runtime, update.effective_user.id if update.effective_user else None)
            ),
        )


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await acknowledge(update)
    runtime = get_runtime(context)
    telegram_user = update.effective_user
    if telegram_user is None:
        return
    await ensure_registered(update, runtime)
    data = update.callback_query.data if update.callback_query else ""
    if data.startswith("nav:"):
        clear_pending(context)
    if data == "nav:main":
        profile = await runtime.users.profile(telegram_user.id)
        await reply_or_edit(
            update,
            welcome_message(
                telegram_user.first_name or "there", profile, is_admin(runtime, telegram_user.id)
            ),
            main_menu(is_admin(runtime, telegram_user.id)),
        )
        return
    if data == "nav:gates":
        if not await check_active(update, runtime):
            return
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>👑 GATES</b>\n{SEPARATOR}\nChoose a controlled testing area.",
            gates_menu(),
        )
        return
    if data == "nav:tools":
        if not await check_active(update, runtime):
            return
        enabled = {
            name
            for name in ("bin_lookup", "synthetic_data", "file_analysis", "fraud_analysis")
            if await runtime.features.is_enabled(name)
        }
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>🛠️ TOOLS</b>\n{SEPARATOR}\nOnly active, implemented tools are shown.",
            tools_menu(enabled),
        )
        return
    if data == "nav:plans":
        from app.handlers.plans import show_plans

        await show_plans(update, context)
        return
    if data == "nav:profile":
        if not await check_active(update, runtime):
            return
        profile = await runtime.users.profile(telegram_user.id)
        if profile is None:
            await reply_or_edit(
                update,
                error_message("Profile unavailable", "Send /start to register your account."),
            )
            return
        await reply_or_edit(update, profile_message(profile), profile_menu())
        return
    if data == "nav:feedback":
        from app.handlers.feedback import begin_feedback

        await begin_feedback(update, context)
        return
    if data == "nav:help":
        await reply_or_edit(update, _HELP_TEXT, main_menu(is_admin(runtime, telegram_user.id)))
        return
    if data == "nav:admin":
        if not is_admin(runtime, telegram_user.id):
            await reply_or_edit(
                update,
                error_message("Unauthorized", "Owner controls require an authorized Telegram ID."),
            )
            return
        from app.handlers.admin import show_admin_home

        await show_admin_home(update, context)


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = get_runtime(context)
    if not await check_active(update, runtime):
        return
    clear_pending(context)
    telegram_user = update.effective_user
    if telegram_user is None:
        return
    profile = await runtime.users.profile(telegram_user.id)
    if profile is None:
        await reply_or_edit(
            update, error_message("Profile unavailable", "Send /start to register your account.")
        )
        return
    await reply_or_edit(update, profile_message(profile), profile_menu())
