from __future__ import annotations

import hashlib
import html

from telegram import Update
from telegram.ext import ContextTypes

from app.handlers.common import (
    acknowledge,
    check_active,
    clear_pending,
    get_runtime,
    reply_or_edit,
    telegram_user_id,
)
from app.keyboards.menus import plan_menu
from app.ui import BRAND, SEPARATOR, error_message, money


async def show_plans(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = get_runtime(context)
    telegram_user = update.effective_user
    if telegram_user is None:
        return
    plans = await runtime.plans.enabled_plans()
    profile = await runtime.users.profile(telegram_user.id)
    lines = [f"{BRAND}\n<b>💎 PLANS &amp; ACCESS</b>\n{SEPARATOR}"]
    options: list[tuple[str, str, bool]] = []
    for plan in plans:
        duration = "No expiry" if plan.duration_days == 0 else f"{plan.duration_days} days"
        current = " · <b>Current</b>" if profile and profile.plan_code == plan.code else ""
        feature_names = (
            ", ".join(
                feature.replace("_", " ").title() for feature in (plan.allowed_features or [])
            )
            or "No features enabled"
        )
        lines.append(
            f"\n💎 <b>{html.escape(plan.name)}</b>{current}\n"
            f"📅 Duration: {duration}\n"
            f"⚡ Daily usage quota: {plan.usage_quota}\n"
            f"🛠️ Features: {html.escape(feature_names)}\n"
            f"💰 Price: {money(plan.price_minor, plan.currency)}"
        )
        if plan.code != "free":
            lines.append("Activation is manual and requires administrator review.")
        options.append((plan.code, plan.name, plan.code != "free"))
    lines.append(
        "\n\nPaid-plan requests do not activate access automatically. Administrators must verify payment independently; screenshots or user claims are not proof."
    )
    await reply_or_edit(update, "\n".join(lines), plan_menu(options))


async def plans_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = get_runtime(context)
    if not await check_active(update, runtime):
        return
    clear_pending(context)
    await show_plans(update, context)


async def plans_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await acknowledge(update)
    clear_pending(context)
    runtime = get_runtime(context)
    if not await check_active(update, runtime):
        return
    data = update.callback_query.data if update.callback_query else ""
    telegram_id = telegram_user_id(update)
    if telegram_id is None:
        return
    if data == "plan:list":
        await show_plans(update, context)
        return
    if data.startswith("plan:req:"):
        code = data.removeprefix("plan:req:")
        if code not in {"basic", "premium"}:
            await reply_or_edit(
                update, error_message("Plan unavailable", "Select an enabled plan from the list.")
            )
            return
        callback_id = update.callback_query.id if update.callback_query else str(update.update_id)
        key = hashlib.sha256(
            f"subscription:{telegram_id}:{callback_id}:{code}".encode()
        ).hexdigest()
        try:
            _, created = await runtime.plans.request_activation(telegram_id, code, key)
        except Exception:
            await reply_or_edit(
                update,
                error_message(
                    "Request unavailable", "That plan could not be requested. Try again later."
                ),
            )
            return
        if created:
            text = (
                f"{BRAND}\n<b>⏳ ACTIVATION REQUEST SUBMITTED</b>\n{SEPARATOR}\n"
                f"Plan: <b>{code.title()}</b>\nAn authorized administrator must review activation. "
                "No plan or payment status was changed automatically."
            )
        else:
            text = (
                f"{BRAND}\n<b>⏳ REQUEST ALREADY PENDING</b>\n{SEPARATOR}\n"
                "An administrator will review your existing request. Do not send card details or payment screenshots."
            )
        await reply_or_edit(update, text, plan_menu([(code, code.title(), False)]))
