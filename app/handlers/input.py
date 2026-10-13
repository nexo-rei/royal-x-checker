from __future__ import annotations

import hashlib
import html
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from app.database.models import SimulationResult, User
from app.handlers.common import (
    check_active,
    get_runtime,
    is_admin,
    rate_allowed,
    reply_or_edit,
    telegram_user_id,
)
from app.keyboards.menus import admin_user_actions, back_menu, confirm_admin
from app.services.errors import ServiceError, Unauthorized
from app.ui import BRAND, SEPARATOR, error_message, money


async def text_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = get_runtime(context)
    user_id = telegram_user_id(update)
    message = update.effective_message
    if user_id is None or message is None or message.text is None:
        return
    action = context.user_data.get("pending_action")
    if not action:
        await reply_or_edit(
            update,
            error_message(
                "Choose a tool",
                "Use the inline menu to start an action. Send /menu to return to the dashboard.",
            ),
            back_menu("nav:main"),
        )
        return
    if action.startswith("admin_"):
        if not is_admin(runtime, user_id):
            context.user_data.pop("pending_action", None)
            await reply_or_edit(
                update,
                error_message(
                    "Unauthorized", "This operation requires an authorized administrator."
                ),
            )
            return
    elif not await check_active(update, runtime):
        return
    if not await rate_allowed(update, runtime):
        return

    text = message.text.strip()
    try:
        if action == "bin_lookup":
            await _handle_bin_lookup(update, context, text)
        elif action == "simulation_amount":
            await _handle_simulation_amount(update, context, text)
        elif action == "feedback":
            await _handle_feedback(update, context, text)
        elif action == "admin_user_lookup":
            await _handle_user_lookup(update, context, text)
        elif action == "admin_credit":
            await _handle_credit_grant(update, context, text)
        elif action == "admin_quota":
            await _handle_quota_update(update, context, text)
        elif action == "admin_price":
            await _handle_price_update(update, context, text)
        elif action == "admin_cost":
            await _handle_cost_update(update, context, text)
        elif action == "file_import":
            await reply_or_edit(
                update,
                "Upload the synthetic CSV document here; plain text is not processed as a file.",
                back_menu("nav:tools"),
            )
        else:
            context.user_data.pop("pending_action", None)
            await reply_or_edit(
                update,
                error_message("Prompt expired", "Open the feature again from the menu."),
                back_menu("nav:main"),
            )
    except ServiceError as exc:
        await reply_or_edit(update, error_message("Request not completed", exc.public_message))
    except ValueError as exc:
        await reply_or_edit(update, error_message("Invalid input", str(exc)))


async def _handle_bin_lookup(
    update: Update, context: ContextTypes.DEFAULT_TYPE, prefix: str
) -> None:
    runtime = get_runtime(context)
    telegram_id = telegram_user_id(update)
    if telegram_id is None:
        return
    if not await runtime.features.is_enabled("bin_lookup"):
        await reply_or_edit(
            update, error_message("Tool disabled", "BIN metadata lookup is disabled.")
        )
        context.user_data.pop("pending_action", None)
        return
    key = _key(telegram_id, update, "bin_lookup")
    reservation = await runtime.credits.reserve_operation(
        telegram_id=telegram_id,
        feature="bin_lookup",
        idempotency_key=key,
        cost=await runtime.credits.feature_cost("bin_lookup"),
    )
    if reservation.duplicate:
        context.user_data.pop("pending_action", None)
        await reply_or_edit(
            update,
            error_message("Already processed", "This lookup update has already been handled."),
            back_menu("nav:tools"),
        )
        return
    try:
        result = await runtime.bin_lookup.lookup(prefix)
    except ServiceError as exc:
        await runtime.credits.fail_operation(
            reservation.usage_id, refund=True, reason_code=type(exc).__name__
        )
        await reply_or_edit(
            update, error_message("Lookup unavailable", exc.public_message), back_menu("nav:tools")
        )
        return
    except Exception as exc:
        await runtime.credits.fail_operation(
            reservation.usage_id, refund=True, reason_code=type(exc).__name__
        )
        await reply_or_edit(
            update,
            error_message(
                "Lookup unavailable", "The metadata provider did not return a verified response."
            ),
            back_menu("nav:tools"),
        )
        return

    await runtime.credits.complete_operation(
        reservation.usage_id,
        {"kind": "bin_metadata", "prefix": result.prefix, "source": result.source},
    )
    context.user_data.pop("pending_action", None)
    fields = [
        f"🔎 Prefix: <code>{html.escape(result.prefix)}</code>",
        f"💳 Network: {html.escape(result.network) if result.network else 'Unavailable'}",
        f"🏷️ Card type: {html.escape(result.card_type) if result.card_type else 'Unavailable'}",
        f"📂 Category: {html.escape(result.category) if result.category else 'Unavailable'}",
        f"🌍 Country: {html.escape(result.country) if result.country else 'Unavailable'}",
        f"🏦 Issuer: {html.escape(result.issuer) if result.issuer else 'Unavailable'}",
    ]
    await reply_or_edit(
        update,
        f"{BRAND}\n<b>🔎 BIN / IIN METADATA</b>\n{SEPARATOR}\n"
        + "\n".join(fields)
        + f"\nSource: {html.escape(result.source)}\n\n"
        "Metadata may be incomplete. This does not verify whether a card is active and is not an authorization check.",
        back_menu("nav:tools"),
    )


async def _handle_simulation_amount(
    update: Update, context: ContextTypes.DEFAULT_TYPE, amount: str
) -> None:
    runtime = get_runtime(context)
    telegram_id = telegram_user_id(update)
    if telegram_id is None:
        return
    if not await runtime.features.is_enabled("simulation"):
        context.user_data.pop("pending_action", None)
        await reply_or_edit(
            update,
            error_message("Simulator disabled", "The simulator is disabled by an administrator."),
        )
        return
    scenario = str(context.user_data.get("simulation_scenario", "random"))
    key = _key(telegram_id, update, "simulation")
    reservation = await runtime.credits.reserve_operation(
        telegram_id=telegram_id,
        feature="simulation",
        idempotency_key=key,
        cost=await runtime.credits.feature_cost("simulation"),
    )
    if reservation.duplicate:
        context.user_data.pop("pending_action", None)
        await reply_or_edit(
            update,
            error_message("Already processed", "This simulation update has already run."),
            back_menu("nav:gates"),
        )
        return
    try:
        result = runtime.simulator.simulate(scenario, amount)
        async with runtime.database.sessions.begin() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            existing = await session.scalar(
                select(SimulationResult).where(SimulationResult.idempotency_key == key)
            )
            if user and existing is None:
                session.add(
                    SimulationResult(
                        user_id=user.id,
                        transaction_ref=result.transaction_id,
                        scenario=result.scenario,
                        outcome=result.outcome,
                        amount_minor=result.amount_minor,
                        currency="USD",
                        risk_score=result.risk_score,
                        explanation=result.explanation,
                        idempotency_key=key,
                    )
                )
        await runtime.credits.complete_operation(
            reservation.usage_id,
            {"kind": "local_simulation", "scenario": result.scenario, "outcome": result.outcome},
        )
    except ValueError as exc:
        await runtime.credits.fail_operation(
            reservation.usage_id, refund=True, reason_code="invalid_amount"
        )
        await reply_or_edit(
            update, error_message("Invalid amount", str(exc)), back_menu("nav:gates")
        )
        return
    except Exception as exc:
        await runtime.credits.fail_operation(
            reservation.usage_id, refund=True, reason_code=type(exc).__name__
        )
        await reply_or_edit(
            update,
            error_message(
                "Simulation failed", "The local simulator could not complete this request."
            ),
            back_menu("nav:gates"),
        )
        return

    context.user_data.pop("pending_action", None)
    context.user_data.pop("simulation_scenario", None)
    outcome_title = {
        "approved": "✅ SIMULATED APPROVAL",
        "declined": "❌ SIMULATED DECLINE",
        "suspicious": "⚠️ SIMULATED SUSPICIOUS",
        "simulated_error": "❌ SIMULATED ERROR",
    }.get(result.outcome, "🧪 SIMULATION RESULT")
    await reply_or_edit(
        update,
        f"{BRAND}\n<b>{outcome_title}</b>\n{SEPARATOR}\n"
        f"Category: <b>Local simulation — not a payment authorization</b>\n"
        f"Scenario: {html.escape(result.scenario.replace('_', ' ').title())}\n"
        f"Amount: {money(result.amount_minor, 'USD')}\n"
        f"Risk score: {result.risk_score}/100\n"
        f"Transaction reference: <code>{html.escape(result.transaction_id)}</code>\n"
        f"Explanation: {html.escape(result.explanation)}",
        back_menu("nav:gates"),
    )


async def _handle_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    runtime = get_runtime(context)
    telegram_id = telegram_user_id(update)
    if telegram_id is None:
        return
    if not await runtime.features.is_enabled("feedback"):
        await reply_or_edit(
            update, error_message("Feedback disabled", "Feedback is temporarily disabled.")
        )
        return
    feedback_id = await runtime.feedback.submit(telegram_id, text)
    context.user_data.pop("pending_action", None)
    await reply_or_edit(
        update,
        f"{BRAND}\n<b>✅ FEEDBACK RECEIVED</b>\n{SEPARATOR}\n"
        f"Reference: <code>#{feedback_id}</code>\nYour message is queued for authorized admin review.",
        back_menu("nav:main"),
    )


async def _handle_user_lookup(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    runtime = get_runtime(context)
    admin_id = telegram_user_id(update)
    if admin_id is None or not is_admin(runtime, admin_id):
        raise Unauthorized()
    if not text.isdecimal() or len(text) > 20:
        raise ValueError("Enter a numeric Telegram user ID.")
    target_id = int(text)
    profile = await runtime.users.profile(target_id)
    if profile is None:
        raise ValueError("No registered user was found for that Telegram ID.")
    context.user_data.pop("pending_action", None)
    await reply_or_edit(
        update,
        f"{BRAND}\n<b>👥 USER RECORD</b>\n{SEPARATOR}\n"
        f"🆔 ID: <code>{profile.telegram_id}</code>\n"
        f"👤 Name: {html.escape(profile.display_name)}\n"
        f"💎 Plan: {html.escape(profile.plan_name)}\n"
        f"⚡ Credits: {profile.credits}\n"
        f"📊 Usage today: {profile.usage_today}/{profile.daily_limit}\n"
        f"🛡️ Status: {html.escape(profile.status)}",
        admin_user_actions(profile.telegram_id, profile.status == "active"),
    )


async def _handle_credit_grant(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    runtime = get_runtime(context)
    admin_id = telegram_user_id(update)
    target_id = context.user_data.get("admin_target_id")
    if admin_id is None or not is_admin(runtime, admin_id) or not isinstance(target_id, int):
        raise Unauthorized()
    if not text.isdecimal():
        raise ValueError("Enter a positive whole-number credit amount.")
    amount = int(text)
    if not 1 <= amount <= 100_000:
        raise ValueError("Credit grant must be between 1 and 100000.")
    context.user_data["pending_action"] = None
    context.user_data["pending_credit_amount"] = amount
    await reply_or_edit(
        update,
        f"{BRAND}\n<b>⚠️ CONFIRM CREDIT GRANT</b>\n{SEPARATOR}\n"
        f"Grant <b>{amount}</b> development credits to user <code>{target_id}</code>?",
        confirm_admin("admin:confirm:credit", "admin:open"),
    )


async def _handle_quota_update(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    runtime = get_runtime(context)
    admin_id = telegram_user_id(update)
    if admin_id is None or not is_admin(runtime, admin_id):
        raise Unauthorized()
    selected_code = context.user_data.get("admin_plan_code")
    if selected_code in {"free", "basic", "premium"} and text.isdecimal():
        code, quota_text = selected_code, text
    else:
        parts = text.split()
        if (
            len(parts) != 2
            or parts[0] not in {"free", "basic", "premium"}
            or not parts[1].isdecimal()
        ):
            raise ValueError("Use: free 10, basic 100, or premium 500.")
        code, quota_text = parts
    quota = int(quota_text)
    if not 0 <= quota <= 100_000:
        raise ValueError("Daily quota must be from 0 to 100000.")
    context.user_data["pending_action"] = None
    context.user_data["pending_plan_quota"] = {"code": code, "quota": quota}
    await reply_or_edit(
        update,
        f"{BRAND}\n<b>⚠️ CONFIRM PLAN QUOTA</b>\n{SEPARATOR}\n"
        f"Set <b>{html.escape(code.title())}</b> to <b>{quota}</b> operations per UTC day?",
        confirm_admin(f"admin:confirm:quota:{code}", "admin:plans"),
    )


async def _handle_price_update(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    runtime = get_runtime(context)
    admin_id = telegram_user_id(update)
    code = context.user_data.get("admin_plan_code")
    if admin_id is None or not is_admin(runtime, admin_id) or code not in {"basic", "premium"}:
        raise Unauthorized()
    try:
        price = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Enter a price such as 9.99 USD.") from exc
    if (
        not price.is_finite()
        or price < 0
        or price > Decimal("1000000")
        or price != price.quantize(Decimal("0.01"))
    ):
        raise ValueError("Price must be from 0.00 to 1000000.00 with at most two decimal places.")
    price_minor = int(price * 100)
    context.user_data["pending_action"] = None
    context.user_data["pending_plan_price"] = {"code": code, "price_minor": price_minor}
    await reply_or_edit(
        update,
        f"{BRAND}\n<b>⚠️ CONFIRM PLAN PRICE</b>\n{SEPARATOR}\n"
        f"Set <b>{html.escape(code.title())}</b> to <b>{money(price_minor)}</b>?",
        confirm_admin(f"admin:confirm:price:{code}", "admin:plans"),
    )


async def _handle_cost_update(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    runtime = get_runtime(context)
    admin_id = telegram_user_id(update)
    feature = context.user_data.get("admin_feature_code")
    if (
        admin_id is None
        or not is_admin(runtime, admin_id)
        or feature not in runtime.settings.feature_costs
    ):
        raise Unauthorized()
    if not text.isdecimal():
        raise ValueError("Enter a whole number of credits from 0 to 10000.")
    cost = int(text)
    if not 0 <= cost <= 10_000:
        raise ValueError("Operation cost must be from 0 to 10000 credits.")
    context.user_data["pending_action"] = None
    context.user_data["pending_feature_cost"] = {"feature": feature, "cost": cost}
    await reply_or_edit(
        update,
        f"{BRAND}\n<b>⚠️ CONFIRM OPERATION COST</b>\n{SEPARATOR}\n"
        f"Set <b>{html.escape(feature)}</b> to <b>{cost} credits</b>?",
        confirm_admin(f"admin:confirm:cost:{feature}", "admin:settings"),
    )


def _key(telegram_id: int, update: Update, feature: str) -> str:
    return hashlib.sha256(f"{telegram_id}:{feature}:{update.update_id}".encode()).hexdigest()
