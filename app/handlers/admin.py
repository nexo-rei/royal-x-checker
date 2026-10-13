from __future__ import annotations

import html
import json

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from app.database.models import Plan
from app.handlers.common import (
    acknowledge,
    clear_pending,
    get_runtime,
    is_admin,
    reply_or_edit,
    telegram_user_id,
)
from app.keyboards.menus import (
    admin_back,
    admin_cost_options,
    admin_features,
    admin_plan_actions,
    admin_plan_list,
    admin_user_actions,
    confirm_admin,
    feedback_actions,
    feedback_queue,
    owner_menu,
    subscription_actions,
    subscription_queue,
)
from app.services.errors import Unauthorized
from app.ui import BRAND, SEPARATOR, error_message, money, utc_display


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = get_runtime(context)
    telegram_id = telegram_user_id(update)
    if not is_admin(runtime, telegram_id):
        await reply_or_edit(update, error_message("Unauthorized", Unauthorized.public_message))
        return
    clear_pending(context)
    await show_admin_home(update, context)


async def show_admin_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = telegram_user_id(update)
    runtime = get_runtime(context)
    if not is_admin(runtime, telegram_id):
        await reply_or_edit(update, error_message("Unauthorized", Unauthorized.public_message))
        return
    await reply_or_edit(
        update,
        f"{BRAND}\n<b>👑 OWNER PANEL</b>\n{SEPARATOR}\n"
        "Administrative operations are permission-checked in every handler and recorded in the audit log.",
        owner_menu(),
    )


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await acknowledge(update)
    runtime = get_runtime(context)
    admin_id = telegram_user_id(update)
    if not is_admin(runtime, admin_id):
        await reply_or_edit(update, error_message("Unauthorized", Unauthorized.public_message))
        return
    data = update.callback_query.data if update.callback_query else ""
    if admin_id is None:
        return
    if not data.startswith("admin:confirm:"):
        clear_pending(context)

    if data in {"admin:open", "admin:home"}:
        await show_admin_home(update, context)
        return
    if data == "admin:stats":
        stats = await runtime.admin.statistics()
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>📊 ADMIN STATISTICS</b>\n{SEPARATOR}\n"
            f"👥 Registered users: <b>{stats.total_users}</b>\n"
            f"✅ Active users: <b>{stats.active_users}</b>\n"
            f"⚡ Operations today: <b>{stats.operations_today}</b>\n"
            f"🧪 Local simulations: <b>{stats.simulation_count}</b>\n"
            f"🛡️ Provider sandbox tests: <b>{stats.sandbox_count}</b>\n"
            f"⏳ Non-final sandbox records: <b>{stats.pending_sandbox}</b>",
            admin_back(),
        )
        return
    if data == "admin:users":
        context.user_data["pending_action"] = "admin_user_lookup"
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>👥 USER MANAGEMENT</b>\n{SEPARATOR}\nEnter a numeric Telegram user ID to inspect status, plan, credits, and usage.",
            admin_back(),
        )
        return
    if data == "admin:plans":
        await _show_plans(update, context)
        return
    if data.startswith("admin:plan:view:"):
        await _show_plan_detail(update, context, data.removeprefix("admin:plan:view:"))
        return
    if data == "admin:settings":
        states = await runtime.features.states()
        status = runtime.stripe_provider.status
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>⚙️ FEATURE SETTINGS</b>\n{SEPARATOR}\n"
            f"Stripe provider status: <b>{html.escape(status)}</b>\n"
            "Enabling a feature does not configure external credentials. Confirm every change.",
            admin_features(states),
        )
        return
    if data.startswith("admin:feature:ask:"):
        feature = data.removeprefix("admin:feature:ask:")
        states = await runtime.features.states()
        if feature not in states:
            await reply_or_edit(
                update,
                error_message("Unknown feature", "That feature is not configurable."),
                admin_back(),
            )
            return
        target = not states[feature]
        context.user_data["admin_feature_confirm"] = {"feature": feature, "enabled": target}
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>⚠️ CONFIRM FEATURE CHANGE</b>\n{SEPARATOR}\n"
            f"Set <b>{html.escape(feature)}</b> to <b>{'ON' if target else 'OFF'}</b>?",
            confirm_admin(f"admin:confirm:feature:{feature}:{int(target)}", "admin:settings"),
        )
        return
    if data.startswith("admin:confirm:feature:"):
        parts = data.split(":")
        if (
            len(parts) != 5
            or parts[3] not in runtime.features.defaults
            or parts[4] not in {"0", "1"}
        ):
            await reply_or_edit(
                update,
                error_message(
                    "Confirmation expired", "Open settings and request the change again."
                ),
                admin_back(),
            )
            return
        feature, enabled = parts[3], parts[4] == "1"
        await runtime.features.set_enabled(feature, enabled, admin_id)
        context.user_data.pop("admin_feature_confirm", None)
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>✅ FEATURE UPDATED</b>\n{SEPARATOR}\n{html.escape(feature)}: {'ON' if enabled else 'OFF'}.",
            admin_features(await runtime.features.states()),
        )
        return
    if data == "admin:costs":
        costs = {
            feature: await runtime.credits.feature_cost(feature)
            for feature in runtime.settings.feature_costs
        }
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>💳 OPERATION COSTS</b>\n{SEPARATOR}\n"
            "Credits are charged only for completed operations. Definite provider or validation failures are refunded.",
            admin_cost_options(costs),
        )
        return
    if data.startswith("admin:cost:"):
        feature = data.removeprefix("admin:cost:")
        if feature not in runtime.settings.feature_costs:
            await reply_or_edit(
                update,
                error_message("Unknown operation", "That cost is not configurable."),
                admin_back(),
            )
            return
        context.user_data["pending_action"] = "admin_cost"
        context.user_data["admin_feature_code"] = feature
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>💳 SET OPERATION COST</b>\n{SEPARATOR}\n"
            f"Enter a whole-number cost from 0 to 10000 credits for <b>{html.escape(feature)}</b>. "
            "The change will require confirmation.",
            admin_back(),
        )
        return
    if data == "admin:quotas":
        context.user_data["pending_action"] = "admin_quota"
        context.user_data.pop("admin_plan_code", None)
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>📊 CONFIGURE PLAN QUOTAS</b>\n{SEPARATOR}\n"
            "Enter a plan code and UTC daily operation limit, for example: <code>free 10</code>. "
            "The change will require confirmation.",
            admin_back(),
        )
        return
    if data.startswith("admin:quota:"):
        code = data.removeprefix("admin:quota:")
        if code not in {"free", "basic", "premium"}:
            await reply_or_edit(
                update, error_message("Unknown plan", "Select an available plan."), admin_back()
            )
            return
        context.user_data["pending_action"] = "admin_quota"
        context.user_data["admin_plan_code"] = code
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>📊 SET PLAN QUOTA</b>\n{SEPARATOR}\n"
            f"Enter a UTC daily operation quota from 0 to 100000 for <b>{html.escape(code.title())}</b>. "
            "The change will require confirmation.",
            admin_back(),
        )
        return
    if data.startswith("admin:price:"):
        code = data.removeprefix("admin:price:")
        if code not in {"basic", "premium"}:
            await reply_or_edit(
                update,
                error_message("Price not editable", "Only paid plans have configurable prices."),
                admin_back(),
            )
            return
        context.user_data["pending_action"] = "admin_price"
        context.user_data["admin_plan_code"] = code
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>💰 SET PLAN PRICE</b>\n{SEPARATOR}\n"
            f"Enter a price in USD, for example <code>9.99</code>, for <b>{html.escape(code.title())}</b>. "
            "The change will require confirmation.",
            admin_back(),
        )
        return
    if data.startswith("admin:plan-toggle:ask:"):
        code = data.removeprefix("admin:plan-toggle:ask:")
        plan = await runtime.plans.get_by_code(code)
        if plan is None:
            await reply_or_edit(
                update, error_message("Plan not found", "Refresh the plan list."), admin_back()
            )
            return
        target = not plan.enabled
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>⚠️ CONFIRM PLAN CHANGE</b>\n{SEPARATOR}\n"
            f"Set <b>{html.escape(plan.name)}</b> to <b>{'ON' if target else 'OFF'}</b>?",
            confirm_admin(f"admin:confirm:plan-toggle:{code}:{int(target)}", "admin:plans"),
        )
        return
    if data.startswith("admin:confirm:plan-toggle:"):
        parts = data.split(":")
        if (
            len(parts) != 5
            or parts[3] not in {"free", "basic", "premium"}
            or parts[4] not in {"0", "1"}
        ):
            await reply_or_edit(
                update,
                error_message("Confirmation expired", "Open plan management and try again."),
                admin_back(),
            )
            return
        code, enabled = parts[3], parts[4] == "1"
        try:
            await runtime.plans.update_plan(code, admin_id=admin_id, enabled=enabled)
        except ValueError as exc:
            await reply_or_edit(
                update, error_message("Plan change rejected", str(exc)), admin_back()
            )
            return
        await _show_plan_detail(update, context, code)
        return
    if data == "admin:feedback":
        rows = await runtime.feedback.open_feedback()
        items = [(item.id, user.telegram_id) for item, user in rows]
        text = f"{BRAND}\n<b>📩 FEEDBACK QUEUE</b>\n{SEPARATOR}\n"
        text += "No new feedback." if not rows else f"Open submissions: <b>{len(rows)}</b>"
        await reply_or_edit(update, text, feedback_queue(items))
        return
    if data.startswith("admin:feedback:view:"):
        try:
            feedback_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await reply_or_edit(
                update,
                error_message("Invalid record", "Feedback reference is invalid."),
                admin_back(),
            )
            return
        result = await runtime.feedback.get(feedback_id)
        if result is None:
            await reply_or_edit(
                update, error_message("Feedback not found", "Refresh the queue."), admin_back()
            )
            return
        feedback, user = result
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>📩 FEEDBACK #{feedback.id}</b>\n{SEPARATOR}\n"
            f"User: <code>{user.telegram_id}</code>\nStatus: {html.escape(feedback.status)}\n"
            f"Received: {utc_display(feedback.created_at)}\n\n{html.escape(feedback.message)}",
            feedback_actions(feedback.id),
        )
        return
    if data.startswith("admin:feedback:resolve:"):
        try:
            feedback_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await reply_or_edit(
                update,
                error_message("Invalid record", "Feedback reference is invalid."),
                admin_back(),
            )
            return
        await runtime.feedback.resolve(feedback_id, admin_id)
        await reply_or_edit(
            update, f"{BRAND}\n<b>✅ FEEDBACK MARKED REVIEWED</b>\n{SEPARATOR}", admin_back()
        )
        return
    if data == "admin:subs":
        rows = await runtime.plans.pending_subscriptions()
        items = [(sub.id, str(user.telegram_id), plan.name) for sub, user, plan in rows]
        text = f"{BRAND}\n<b>🧾 SUBSCRIPTION REQUESTS</b>\n{SEPARATOR}\n"
        text += "No pending activation requests." if not rows else f"Pending: <b>{len(rows)}</b>"
        await reply_or_edit(update, text, subscription_queue(items))
        return
    if data.startswith("admin:sub:view:"):
        try:
            subscription_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await reply_or_edit(
                update,
                error_message("Invalid request", "Subscription reference is invalid."),
                admin_back(),
            )
            return
        summary = await runtime.plans.request_summary(subscription_id)
        if summary is None:
            await reply_or_edit(
                update,
                error_message("Request not found", "Refresh the subscription queue."),
                admin_back(),
            )
            return
        subscription, user, plan = summary
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>🧾 ACTIVATION REQUEST #{subscription.id}</b>\n{SEPARATOR}\n"
            f"User: <code>{user.telegram_id}</code>\nPlan: <b>{html.escape(plan.name)}</b>\n"
            f"Status: {html.escape(subscription.status)}\nRequested: {utc_display(subscription.requested_at)}\n\n"
            "Approve only after independently verifying the transaction through the authorized payment channel. A screenshot or claim is not proof.",
            subscription_actions(subscription.id),
        )
        return
    if data.startswith("admin:sub:approve:ask:") or data.startswith("admin:sub:reject:ask:"):
        parts = data.split(":")
        if len(parts) != 5:
            await reply_or_edit(
                update,
                error_message("Invalid request", "Subscription reference is invalid."),
                admin_back(),
            )
            return
        decision = parts[2]
        try:
            subscription_id = int(parts[4])
        except ValueError:
            await reply_or_edit(
                update,
                error_message("Invalid request", "Subscription reference is invalid."),
                admin_back(),
            )
            return
        label = "approve" if decision == "approve" else "reject"
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>⚠️ CONFIRM SUBSCRIPTION {label.upper()}</b>\n{SEPARATOR}\n"
            f"Confirm {label} for request <code>#{subscription_id}</code>?",
            confirm_admin(f"admin:confirm:sub:{label}:{subscription_id}", "admin:subs"),
        )
        return
    if data.startswith("admin:confirm:sub:"):
        parts = data.split(":")
        if len(parts) != 5 or parts[3] not in {"approve", "reject"}:
            await reply_or_edit(
                update,
                error_message("Confirmation expired", "Reopen the subscription request."),
                admin_back(),
            )
            return
        try:
            subscription_id = int(parts[4])
        except ValueError:
            await reply_or_edit(
                update,
                error_message("Invalid request", "Subscription reference is invalid."),
                admin_back(),
            )
            return
        await runtime.plans.review_subscription(subscription_id, admin_id, parts[3] == "approve")
        await reply_or_edit(
            update, f"{BRAND}\n<b>✅ SUBSCRIPTION REQUEST REVIEWED</b>\n{SEPARATOR}", admin_back()
        )
        return
    if data == "admin:audit":
        rows = await runtime.admin.recent_audit()
        lines = [f"{BRAND}\n<b>📋 AUDIT LOG</b>\n{SEPARATOR}"]
        if not rows:
            lines.append("No administrative changes recorded yet.")
        for row in rows:
            details = json.dumps(row.details or {}, ensure_ascii=False, sort_keys=True)
            lines.append(
                f"• {utc_display(row.created_at)} · <b>{html.escape(row.action)}</b>\n"
                f"  actor <code>{row.actor_telegram_id}</code> · {html.escape(row.subject or '—')}\n"
                f"  <code>{html.escape(details[:240])}</code>"
            )
        await reply_or_edit(update, "\n".join(lines), admin_back())
        return
    if data == "admin:sandbox":
        rows = await runtime.admin.recent_sandbox()
        lines = [f"{BRAND}\n<b>🧾 SANDBOX SUMMARY</b>\n{SEPARATOR}"]
        if not rows:
            lines.append("No provider sandbox transactions recorded.")
        for row in rows:
            lines.append(
                f"• {utc_display(row.created_at)} · {html.escape(row.status)} / {html.escape(row.outcome)}\n"
                f"  {money(row.amount_minor, row.currency)} · ref <code>{html.escape(row.provider_ref or 'unconfirmed')}</code>"
            )
        await reply_or_edit(update, "\n".join(lines), admin_back())
        return
    if data.startswith("admin:credit:"):
        try:
            target_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await reply_or_edit(
                update,
                error_message("Invalid user", "That user reference is invalid."),
                admin_back(),
            )
            return
        user = await runtime.users.by_telegram_id(target_id)
        if user is None:
            await reply_or_edit(
                update,
                error_message("User not found", "No registered user has that ID."),
                admin_back(),
            )
            return
        context.user_data["pending_action"] = "admin_credit"
        context.user_data["admin_target_id"] = target_id
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>➕ DEVELOPMENT CREDITS</b>\n{SEPARATOR}\n"
            f"Enter an amount from 1 to 100000 for user <code>{target_id}</code>. A confirmation is required before granting.",
            admin_user_actions(target_id, user.status == "active"),
        )
        return
    if data.startswith("admin:user-status:ask:"):
        parts = data.split(":")
        if len(parts) != 5 or parts[3] not in {"suspend", "activate"}:
            await reply_or_edit(
                update,
                error_message("Invalid action", "User status action is invalid."),
                admin_back(),
            )
            return
        try:
            target_id = int(parts[4])
        except ValueError:
            await reply_or_edit(
                update,
                error_message("Invalid user", "That user reference is invalid."),
                admin_back(),
            )
            return
        status = "suspended" if parts[3] == "suspend" else "active"
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>⚠️ CONFIRM ACCOUNT CHANGE</b>\n{SEPARATOR}\n"
            f"Set user <code>{target_id}</code> to <b>{status}</b>?",
            confirm_admin(f"admin:confirm:user-status:{parts[3]}:{target_id}", "admin:users"),
        )
        return
    if data.startswith("admin:confirm:user-status:"):
        parts = data.split(":")
        if len(parts) != 5 or parts[3] not in {"suspend", "activate"}:
            await reply_or_edit(
                update,
                error_message("Confirmation expired", "Open user management again."),
                admin_back(),
            )
            return
        try:
            target_id = int(parts[4])
        except ValueError:
            await reply_or_edit(
                update,
                error_message("Invalid user", "That user reference is invalid."),
                admin_back(),
            )
            return
        status = "suspended" if parts[3] == "suspend" else "active"
        changed = await runtime.users.set_status(target_id, status, admin_id)
        if not changed:
            await reply_or_edit(
                update,
                error_message("User not found", "No registered user has that ID."),
                admin_back(),
            )
            return
        profile = await runtime.users.profile(target_id)
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>✅ ACCOUNT STATUS UPDATED</b>\n{SEPARATOR}\n"
            f"User <code>{target_id}</code> is now <b>{status}</b>.",
            admin_user_actions(target_id, profile.status == "active" if profile else False),
        )
        return
    if data == "admin:confirm:credit":
        target_id = context.user_data.get("admin_target_id")
        amount = context.user_data.get("pending_credit_amount")
        if not isinstance(target_id, int) or not isinstance(amount, int):
            await reply_or_edit(
                update,
                error_message(
                    "Confirmation expired", "Open the user record and start the grant again."
                ),
                admin_back(),
            )
            return
        callback_id = update.callback_query.id if update.callback_query else str(update.update_id)
        key = f"admin-credit:{admin_id}:{callback_id}:{target_id}"
        balance = await runtime.credits.grant_credits(
            target_telegram_id=target_id,
            amount=amount,
            admin_id=admin_id,
            idempotency_key=key,
        )
        await runtime.users.record_audit(
            admin_id,
            "admin_credits_granted",
            subject=f"telegram:{target_id}",
            details={"amount": amount},
        )
        context.user_data.pop("admin_target_id", None)
        context.user_data.pop("pending_credit_amount", None)
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>✅ DEVELOPMENT CREDITS GRANTED</b>\n{SEPARATOR}\n"
            f"User: <code>{target_id}</code>\nNew balance: <b>{balance}</b>\n"
            "This administrator-approved grant is recorded in the credit ledger and audit log.",
            admin_back(),
        )
        return
    if data.startswith("admin:confirm:quota:"):
        code = data.removeprefix("admin:confirm:quota:")
        pending = context.user_data.get("pending_plan_quota")
        if (
            code not in {"free", "basic", "premium"}
            or not isinstance(pending, dict)
            or pending.get("code") != code
        ):
            await reply_or_edit(
                update,
                error_message("Confirmation expired", "Enter the plan quota again."),
                admin_back(),
            )
            return
        quota = pending.get("quota")
        if not isinstance(quota, int):
            await reply_or_edit(
                update, error_message("Invalid quota", "Enter the quota again."), admin_back()
            )
            return
        await runtime.plans.update_plan(code, admin_id=admin_id, usage_quota=quota)
        context.user_data.pop("pending_plan_quota", None)
        context.user_data.pop("admin_plan_code", None)
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>✅ PLAN QUOTA UPDATED</b>\n{SEPARATOR}\n{html.escape(code.title())}: {quota} operations per UTC day.",
            admin_back(),
        )
        return
    if data.startswith("admin:confirm:price:"):
        code = data.removeprefix("admin:confirm:price:")
        pending = context.user_data.get("pending_plan_price")
        if (
            code not in {"basic", "premium"}
            or not isinstance(pending, dict)
            or pending.get("code") != code
        ):
            await reply_or_edit(
                update,
                error_message("Confirmation expired", "Enter the plan price again."),
                admin_back(),
            )
            return
        price_minor = pending.get("price_minor")
        if not isinstance(price_minor, int):
            await reply_or_edit(
                update, error_message("Invalid price", "Enter the plan price again."), admin_back()
            )
            return
        await runtime.plans.update_plan(code, admin_id=admin_id, price_minor=price_minor)
        context.user_data.pop("pending_plan_price", None)
        context.user_data.pop("admin_plan_code", None)
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>✅ PLAN PRICE UPDATED</b>\n{SEPARATOR}\n{html.escape(code.title())}: {money(price_minor)}",
            admin_back(),
        )
        return
    if data.startswith("admin:confirm:cost:"):
        feature = data.removeprefix("admin:confirm:cost:")
        pending = context.user_data.get("pending_feature_cost")
        if (
            feature not in runtime.settings.feature_costs
            or not isinstance(pending, dict)
            or pending.get("feature") != feature
        ):
            await reply_or_edit(
                update,
                error_message("Confirmation expired", "Enter the operation cost again."),
                admin_back(),
            )
            return
        cost = pending.get("cost")
        if not isinstance(cost, int):
            await reply_or_edit(
                update,
                error_message("Invalid cost", "Enter the operation cost again."),
                admin_back(),
            )
            return
        await runtime.features.set_cost(feature, cost, admin_id)
        context.user_data.pop("pending_feature_cost", None)
        context.user_data.pop("admin_feature_code", None)
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>✅ OPERATION COST UPDATED</b>\n{SEPARATOR}\n{html.escape(feature)}: {cost} credits.",
            admin_back(),
        )


async def _show_plans(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = get_runtime(context)
    async with runtime.database.sessions() as session:
        rows = await session.scalars(select(Plan).order_by(Plan.price_minor))
        plans = list(rows.all())
    items = [(plan.code, plan.name, plan.enabled) for plan in plans]
    await reply_or_edit(
        update,
        f"{BRAND}\n<b>💎 PLAN MANAGEMENT</b>\n{SEPARATOR}\n"
        "Select a plan to edit its price, daily quota, or enabled state.",
        admin_plan_list(items),
    )


async def _show_plan_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    runtime = get_runtime(context)
    plan = await runtime.plans.get_by_code(code)
    if plan is None:
        await reply_or_edit(
            update, error_message("Plan not found", "Refresh the plan list."), admin_back()
        )
        return
    duration = "No expiry" if plan.duration_days == 0 else f"{plan.duration_days} days"
    await reply_or_edit(
        update,
        f"{BRAND}\n<b>💎 {html.escape(plan.name.upper())} PLAN</b>\n{SEPARATOR}\n"
        f"Status: <b>{'enabled' if plan.enabled else 'disabled'}</b>\n"
        f"Price: <b>{money(plan.price_minor, plan.currency)}</b>\n"
        f"Duration: {duration}\n"
        f"Daily quota: <b>{plan.usage_quota}</b>\n"
        f"Enabled feature count: {len(plan.allowed_features or [])}",
        admin_plan_actions(code, plan.enabled),
    )
