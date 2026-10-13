from __future__ import annotations

import hashlib
import html
import logging
from collections import Counter
from typing import Any

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from app.database.models import SandboxTransaction, SimulationResult, User
from app.handlers.common import (
    acknowledge,
    check_active,
    clear_pending,
    get_runtime,
    rate_allowed,
    reply_or_edit,
    telegram_user_id,
)
from app.keyboards.menus import auth_menu, back_menu, gates_menu, simulation_menu
from app.services.errors import (
    IntegrationNotConfigured,
    ProviderFailure,
    ProviderIndeterminate,
    SandboxDisabled,
    ServiceError,
)
from app.ui import BRAND, SEPARATOR, error_message, money

logger = logging.getLogger(__name__)


def escape_html(value: object) -> str:
    return html.escape(str(value), quote=True)


async def gates_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await acknowledge(update)
    clear_pending(context)
    runtime = get_runtime(context)
    if not await check_active(update, runtime):
        return
    telegram_id = telegram_user_id(update)
    data = update.callback_query.data if update.callback_query else ""
    if telegram_id is None:
        return

    if data == "gate:auth":
        status = runtime.stripe_provider.status
        pending = await _pending_sandbox(runtime, telegram_id)
        if status == "disabled":
            detail = (
                "⚠️ Stripe test mode is disabled. No provider request will be sent.\n"
                "Set STRIPE_SANDBOX_ENABLED=true and configure a test-mode key to enable it."
            )
        elif status == "not_configured":
            detail = (
                "⚠️ The official Stripe sandbox adapter is implemented, but credentials are missing.\n"
                "Set STRIPE_SECRET_KEY to an sk_test_ key and explicitly enable sandbox mode."
            )
        elif status != "ready":
            detail = "⚠️ Stripe sandbox configuration is invalid. Only test-mode keys are accepted."
        else:
            detail = "✅ Stripe test mode is configured. Only the documented pm_card_visa test PaymentMethod is accepted."
        if pending:
            pending_state = (
                "is being processed"
                if pending.status == "processing"
                else "is awaiting confirmation"
            )
            detail += (
                f"\n\n⚠️ A request {pending_state}. Reference: <code>#{pending.usage_id}</code>"
            )
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>🧪 AUTH — AUTHORIZED SANDBOX</b>\n{SEPARATOR}\n"
            f"Provider: <b>Stripe Test Mode</b>\n{detail}\n\n"
            "Every operation is labelled as a test transaction. No card numbers or CVVs are collected.",
            auth_menu(
                status == "ready" and pending is None,
                pending.usage_id if pending and pending.status == "indeterminate" else None,
            ),
        )
        return

    if data == "gate:sim":
        if not await runtime.features.is_enabled("simulation"):
            await reply_or_edit(
                update,
                error_message(
                    "Feature disabled", "The local simulator is disabled by an administrator."
                ),
            )
            return
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>💳 CHARGE — LOCAL SIMULATOR</b>\n{SEPARATOR}\n"
            "Choose a synthetic scenario, then enter a test amount.\n\n"
            "These outcomes are generated locally and are never real payment authorizations.",
            simulation_menu(runtime.settings.max_test_count),
        )
        return

    if data.startswith("sim:batch:"):
        try:
            count = int(data.rsplit(":", 1)[1])
        except ValueError:
            await reply_or_edit(
                update, error_message("Invalid batch", "Choose a displayed test count.")
            )
            return
        await _run_simulation_batch(update, context, count)
        return

    if data.startswith("sim:scenario:"):
        if not await runtime.features.is_enabled("simulation"):
            await reply_or_edit(
                update,
                error_message(
                    "Feature disabled", "The local simulator is disabled by an administrator."
                ),
            )
            return
        if not await rate_allowed(update, runtime):
            return
        scenario = data.removeprefix("sim:scenario:")
        if scenario not in {"random", *runtime.simulator.SCENARIOS}:
            await reply_or_edit(
                update,
                error_message("Invalid scenario", "Choose one of the listed simulator scenarios."),
            )
            return
        context.user_data["pending_action"] = "simulation_amount"
        context.user_data["simulation_scenario"] = scenario
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>🧪 SIMULATION AMOUNT</b>\n{SEPARATOR}\n"
            f"Scenario: <b>{escape_html(scenario.replace('_', ' ').title())}</b>\n"
            f"Enter a synthetic USD amount up to {runtime.settings.max_simulation_amount_minor / 100:.2f}. "
            "No provider request will be made.",
            back_menu("nav:gates"),
        )
        return

    if data == "gate:fraud":
        if not await runtime.features.is_enabled("fraud_analysis"):
            await reply_or_edit(
                update,
                error_message(
                    "Feature disabled", "Fraud analytics is disabled by an administrator."
                ),
            )
            return
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>🛡️ FRAUD LAB</b>\n{SEPARATOR}\n"
            "Transparent rules-based heuristics for synthetic records—not a guaranteed fraud determination.\n"
            f"High amount: &gt; {runtime.settings.high_amount_threshold:.2f} USD\n"
            f"Repeated attempts: ≥ {runtime.settings.repeated_attempt_threshold}\n"
            f"Customer frequency: ≥ {runtime.settings.frequency_threshold}\n"
            f"Rapid location window: {runtime.settings.location_change_window_minutes} minutes\n\n"
            "Use Tools → Fraud Analytics to analyze generated or validated synthetic records.",
            back_menu("nav:gates"),
        )
        return

    if data == "gate:history":
        simulations, sandbox = await runtime.history.recent(telegram_id)
        lines = [f"{BRAND}\n<b>📜 TEST HISTORY</b>\n{SEPARATOR}"]
        if not simulations and not sandbox:
            lines.append("No test or simulation history yet.")
        if simulations:
            lines.append("<b>Local simulations — synthetic only</b>")
            for item in simulations[:8]:
                lines.append(
                    f"• {escape_html(item.scenario.replace('_', ' '))} · {escape_html(item.outcome)} · "
                    f"{money(item.amount_minor, item.currency)} · risk {item.risk_score}/100 · "
                    f"ref <code>{escape_html(item.transaction_ref)}</code>"
                )
        if sandbox:
            lines.append("\n<b>Provider-confirmed sandbox</b>")
            for item in sandbox[:8]:
                lines.append(
                    f"• {escape_html(item.provider)} · {escape_html(item.status)} · "
                    f"{money(item.amount_minor, item.currency)} · ref <code>{escape_html(item.provider_ref or 'pending')}</code>"
                )
        await reply_or_edit(update, "\n".join(lines), back_menu("nav:gates"))
        return

    if data.startswith("auth:stripe:"):
        try:
            amount_minor = int(data.rsplit(":", 1)[1])
        except ValueError:
            await reply_or_edit(
                update, error_message("Invalid request", "The selected sandbox amount is invalid.")
            )
            return
        if amount_minor not in {100, 500}:
            await reply_or_edit(
                update,
                error_message("Invalid request", "Only the displayed test amounts are supported."),
            )
            return
        await _run_stripe_test(update, context, amount_minor)
        return

    if data.startswith("auth:retry:"):
        try:
            usage_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await reply_or_edit(
                update, error_message("Invalid request", "That sandbox retry reference is invalid.")
            )
            return
        await _retry_stripe_test(update, context, usage_id)


async def _run_simulation_batch(
    update: Update, context: ContextTypes.DEFAULT_TYPE, count: int
) -> None:
    runtime = get_runtime(context)
    telegram_id = telegram_user_id(update)
    if telegram_id is None:
        return
    if not await runtime.features.is_enabled("simulation"):
        await reply_or_edit(
            update,
            error_message(
                "Feature disabled", "The local simulator is disabled by an administrator."
            ),
        )
        return
    if not 1 <= count <= runtime.settings.max_test_count:
        await reply_or_edit(
            update,
            error_message(
                "Batch limit",
                f"Choose between 1 and {runtime.settings.max_test_count} synthetic transactions.",
            ),
        )
        return
    if not await rate_allowed(update, runtime):
        return
    key = _request_key(telegram_id, update)
    try:
        reservation = await runtime.credits.reserve_operation(
            telegram_id=telegram_id,
            feature="simulation",
            idempotency_key=key,
            cost=await runtime.credits.feature_cost("simulation"),
        )
    except ServiceError as exc:
        await reply_or_edit(update, error_message("Batch not started", exc.public_message))
        return
    if reservation.duplicate:
        await reply_or_edit(
            update,
            error_message(
                "Already processed",
                "This batch callback has already been processed; no duplicate results were generated.",
            ),
            back_menu("nav:gates"),
        )
        return

    await reply_or_edit(
        update,
        f"{BRAND}\n<b>⏳ LOCAL SIMULATION BATCH</b>\n{SEPARATOR}\n"
        f"Generating {count} bounded synthetic outcomes. No provider API will be called.",
        back_menu("nav:gates"),
    )
    try:
        outcomes = runtime.simulator.simulate_batch(count, runtime.settings.max_test_count)
        async with runtime.database.sessions.begin() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                raise RuntimeError("User record disappeared during local simulation")
            for index, item in enumerate(outcomes):
                item_key = hashlib.sha256(f"{key}:{index}".encode()).hexdigest()
                existing = await session.scalar(
                    select(SimulationResult).where(SimulationResult.idempotency_key == item_key)
                )
                if existing is None:
                    session.add(
                        SimulationResult(
                            user_id=user.id,
                            transaction_ref=item.transaction_id,
                            scenario=item.scenario,
                            outcome=item.outcome,
                            amount_minor=item.amount_minor,
                            currency="USD",
                            risk_score=item.risk_score,
                            explanation=item.explanation,
                            idempotency_key=item_key,
                        )
                    )
        counts = Counter(item.outcome for item in outcomes)
        await runtime.credits.complete_operation(
            reservation.usage_id,
            {"kind": "local_simulation_batch", "count": count, "outcomes": dict(counts)},
        )
    except Exception as exc:
        await runtime.credits.fail_operation(
            reservation.usage_id, refund=True, reason_code=type(exc).__name__
        )
        logger.error("Local simulation batch failed (%s)", type(exc).__name__)
        await reply_or_edit(
            update,
            error_message("Batch failed", "No unverified result was returned."),
            back_menu("nav:gates"),
        )
        return

    total_minor = sum(item.amount_minor for item in outcomes)
    summary = (
        f"✅ Simulated approvals: <b>{counts.get('approved', 0)}</b>\n"
        f"❌ Simulated declines: <b>{counts.get('declined', 0)}</b>\n"
        f"⚠️ Simulated suspicious: <b>{counts.get('suspicious', 0)}</b>\n"
        f"❌ Simulated errors: <b>{counts.get('simulated_error', 0)}</b>\n"
        f"Average risk score: <b>{sum(item.risk_score for item in outcomes) // len(outcomes)}/100</b>\n"
        f"Synthetic amount total: <b>{money(total_minor, 'USD')}</b>"
    )
    await reply_or_edit(
        update,
        f"{BRAND}\n<b>🧪 LOCAL BATCH SUMMARY</b>\n{SEPARATOR}\n"
        f"Transactions: <b>{len(outcomes)}</b>\n{summary}\n\n"
        "All outcomes are generated locally. They are not provider results or payment authorizations.",
        back_menu("nav:gates"),
    )
    message = update.effective_message or (
        update.callback_query.message if update.callback_query else None
    )
    if message is not None:
        from app.utils.telegram_files import send_temporary_document

        report = runtime.simulator.export_batch_csv(outcomes)
        await send_temporary_document(
            message,
            report,
            "royal_x_simulation_batch.csv",
            "Local synthetic outcomes only — no provider was contacted.",
        )


async def _run_stripe_test(
    update: Update, context: ContextTypes.DEFAULT_TYPE, amount_minor: int
) -> None:
    runtime = get_runtime(context)
    telegram_id = telegram_user_id(update)
    if telegram_id is None:
        return
    if runtime.stripe_provider.status == "disabled":
        await reply_or_edit(
            update, error_message("Sandbox disabled", SandboxDisabled.public_message)
        )
        return
    if runtime.stripe_provider.status != "ready":
        await reply_or_edit(
            update, error_message("Configuration required", IntegrationNotConfigured.public_message)
        )
        return
    existing_pending = await _pending_sandbox(runtime, telegram_id)
    if existing_pending:
        retry_id = existing_pending.usage_id if existing_pending.status == "indeterminate" else None
        await reply_or_edit(
            update,
            error_message(
                "Sandbox request pending",
                "Resolve the existing request before starting another test transaction.",
            ),
            auth_menu(False, retry_id),
        )
        return
    if not await rate_allowed(update, runtime):
        return

    key = _request_key(telegram_id, update)
    cost = await runtime.credits.feature_cost("stripe_sandbox")
    try:
        reservation = await runtime.credits.reserve_operation(
            telegram_id=telegram_id,
            feature="stripe_sandbox",
            idempotency_key=key,
            cost=cost,
        )
    except ServiceError as exc:
        await reply_or_edit(update, error_message("Request not completed", exc.public_message))
        return
    if reservation.duplicate:
        await _show_duplicate_sandbox(
            update, runtime, telegram_id, reservation.usage_id, reservation.status
        )
        return

    await _save_sandbox_transaction(
        runtime,
        telegram_id,
        reservation.usage_id,
        amount_minor,
        key,
        provider_ref=None,
        status="processing",
        outcome="pending",
        safe_metadata={"test_mode": True},
    )
    await reply_or_edit(
        update,
        f"{BRAND}\n<b>⏳ STRIPE TEST MODE</b>\n{SEPARATOR}\n"
        f"Submitting {money(amount_minor)} with Stripe's documented test PaymentMethod.\n"
        "This operation is explicitly sandbox-only.",
        back_menu("nav:gates"),
    )
    try:
        result = await runtime.stripe_provider.run_test_payment(
            amount_minor=amount_minor,
            currency="usd",
            idempotency_key=key,
        )
    except ProviderIndeterminate:
        await _hold_for_retry(runtime, telegram_id, reservation.usage_id, amount_minor, key)
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>⚠️ RESULT UNCONFIRMED</b>\n{SEPARATOR}\n"
            "The request may have reached Stripe, but a verified status was not received. "
            "Credits remain reserved, not re-deducted. Retry the same idempotent request from AUTH. "
            "No approval is claimed.",
            auth_menu(True, reservation.usage_id),
        )
        return
    except (SandboxDisabled, IntegrationNotConfigured, ProviderFailure) as exc:
        await runtime.credits.fail_operation(
            reservation.usage_id,
            refund=True,
            reason_code=type(exc).__name__,
        )
        await _update_sandbox_failure(runtime, key, "provider_error")
        await reply_or_edit(
            update, error_message("Sandbox not completed", exc.public_message), auth_menu(False)
        )
        return
    except Exception as exc:
        logger.error("Stripe sandbox operation failed (%s)", type(exc).__name__)
        await _hold_for_retry(runtime, telegram_id, reservation.usage_id, amount_minor, key)
        await reply_or_edit(
            update,
            error_message(
                "Result unconfirmed",
                "Provider state is uncertain. Reserved credits are held safely; retry the same request from AUTH.",
            ),
            auth_menu(True, reservation.usage_id),
        )
        return

    await _save_sandbox_transaction(
        runtime,
        telegram_id,
        reservation.usage_id,
        amount_minor,
        key,
        provider_ref=result.reference,
        status=result.status,
        outcome=result.outcome,
        safe_metadata={"method": result.method_label, "test_mode": True},
    )
    await runtime.credits.complete_operation(
        reservation.usage_id,
        {
            "provider": result.provider,
            "provider_ref": result.reference,
            "status": result.status,
            "amount_minor": amount_minor,
        },
    )
    outcome_line = _outcome_line(result.outcome, result.status)
    await reply_or_edit(
        update,
        f"{BRAND}\n<b>🧪 STRIPE SANDBOX RESULT</b>\n{SEPARATOR}\n"
        f"{outcome_line}\nAmount: {money(result.amount_minor, result.currency)}\n"
        f"Provider: Stripe Test Mode\nReference: <code>{escape_html(result.reference)}</code>\n"
        "No live authorization occurred.",
        back_menu("nav:gates"),
    )


async def _retry_stripe_test(
    update: Update, context: ContextTypes.DEFAULT_TYPE, usage_id: int
) -> None:
    runtime = get_runtime(context)
    telegram_id = telegram_user_id(update)
    if telegram_id is None:
        return
    if runtime.stripe_provider.status != "ready":
        await reply_or_edit(
            update, error_message("Configuration required", IntegrationNotConfigured.public_message)
        )
        return
    if not await rate_allowed(update, runtime):
        return
    pending = await runtime.credits.get_pending_operation(telegram_id, usage_id, "stripe_sandbox")
    if pending is None or pending.status != "indeterminate":
        await reply_or_edit(
            update,
            error_message(
                "No retry pending", "This request is not waiting for an idempotent retry."
            ),
        )
        return
    amount_minor = pending.details.get("amount_minor")
    if not isinstance(amount_minor, int):
        amount_minor = await _sandbox_amount_for_usage(
            runtime, telegram_id, pending.idempotency_key
        )
    if amount_minor not in {100, 500}:
        await reply_or_edit(
            update,
            error_message("Retry unavailable", "The saved sandbox request could not be verified."),
        )
        return
    try:
        resumed = await runtime.credits.resume_indeterminate(
            telegram_id=telegram_id,
            usage_id=usage_id,
            feature="stripe_sandbox",
        )
    except ServiceError as exc:
        await reply_or_edit(update, error_message("Retry unavailable", exc.public_message))
        return
    except ValueError:
        await reply_or_edit(
            update, error_message("Retry unavailable", "That request is already being processed.")
        )
        return

    await reply_or_edit(
        update,
        f"{BRAND}\n<b>⏳ RETRYING SAME SANDBOX REQUEST</b>\n{SEPARATOR}\n"
        "Stripe idempotency is being reused; the original request will not be duplicated.",
        back_menu("nav:gates"),
    )
    try:
        result = await runtime.stripe_provider.run_test_payment(
            amount_minor=amount_minor,
            currency="usd",
            idempotency_key=resumed.idempotency_key,
        )
    except ProviderIndeterminate:
        await runtime.credits.mark_indeterminate(usage_id, {"amount_minor": amount_minor})
        await reply_or_edit(
            update,
            error_message("Still unconfirmed", "No approval is claimed. Try again later."),
            auth_menu(True, usage_id),
        )
        return
    except (SandboxDisabled, IntegrationNotConfigured, ProviderFailure) as exc:
        await runtime.credits.fail_operation(usage_id, refund=True, reason_code=type(exc).__name__)
        await _update_sandbox_failure(runtime, resumed.idempotency_key, "provider_error")
        await reply_or_edit(
            update, error_message("Retry not completed", exc.public_message), auth_menu(False)
        )
        return
    except Exception as exc:
        logger.error("Stripe sandbox retry failed (%s)", type(exc).__name__)
        await runtime.credits.mark_indeterminate(usage_id, {"amount_minor": amount_minor})
        await reply_or_edit(
            update,
            error_message("Still unconfirmed", "No approval is claimed. Retry later."),
            auth_menu(True, usage_id),
        )
        return

    await _save_sandbox_transaction(
        runtime,
        telegram_id,
        usage_id,
        amount_minor,
        resumed.idempotency_key,
        provider_ref=result.reference,
        status=result.status,
        outcome=result.outcome,
        safe_metadata={"method": result.method_label, "test_mode": True},
    )
    await runtime.credits.complete_operation(
        usage_id,
        {
            "provider": result.provider,
            "provider_ref": result.reference,
            "status": result.status,
            "amount_minor": amount_minor,
        },
    )
    await reply_or_edit(
        update,
        f"{BRAND}\n<b>🧪 VERIFIED STRIPE TEST RESULT</b>\n{SEPARATOR}\n"
        f"{_outcome_line(result.outcome, result.status)}\nAmount: {money(result.amount_minor, result.currency)}\n"
        f"Reference: <code>{escape_html(result.reference)}</code>\nTest mode only; no live authorization.",
        back_menu("nav:gates"),
    )


async def _hold_for_retry(
    runtime, telegram_id: int, usage_id: int, amount_minor: int, key: str
) -> None:
    await runtime.credits.mark_indeterminate(usage_id, {"amount_minor": amount_minor})
    await _save_sandbox_transaction(
        runtime,
        telegram_id,
        usage_id,
        amount_minor,
        key,
        provider_ref=None,
        status="indeterminate",
        outcome="integration_error",
        safe_metadata={"reason": "provider_response_unconfirmed", "test_mode": True},
    )


async def _save_sandbox_transaction(
    runtime: Any,
    telegram_id: int,
    usage_id: int,
    amount_minor: int,
    key: str,
    *,
    provider_ref: str | None,
    status: str,
    outcome: str,
    safe_metadata: dict[str, Any],
) -> None:
    async with runtime.database.sessions.begin() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            return
        existing = await session.scalar(
            select(SandboxTransaction).where(SandboxTransaction.idempotency_key == key)
        )
        if existing:
            existing.usage_id = usage_id
            existing.provider_ref = provider_ref or existing.provider_ref
            existing.status = status
            existing.outcome = outcome
            existing.safe_metadata = safe_metadata
            return
        session.add(
            SandboxTransaction(
                user_id=user.id,
                usage_id=usage_id,
                provider="stripe_test_mode",
                provider_ref=provider_ref,
                status=status,
                outcome=outcome,
                amount_minor=amount_minor,
                currency="USD",
                test_mode=True,
                idempotency_key=key,
                safe_metadata=safe_metadata,
            )
        )


async def _update_sandbox_failure(runtime, key: str, reason: str) -> None:
    async with runtime.database.sessions.begin() as session:
        row = await session.scalar(
            select(SandboxTransaction).where(SandboxTransaction.idempotency_key == key)
        )
        if row:
            row.status = "failed"
            row.outcome = "integration_error"
            row.safe_metadata = {"reason": reason, "test_mode": True}


async def _pending_sandbox(runtime, telegram_id: int) -> SandboxTransaction | None:
    async with runtime.database.sessions() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            return None
        return await session.scalar(
            select(SandboxTransaction)
            .where(
                SandboxTransaction.user_id == user.id,
                SandboxTransaction.status.in_(("processing", "indeterminate")),
                SandboxTransaction.usage_id.is_not(None),
            )
            .order_by(SandboxTransaction.created_at.desc())
            .limit(1)
        )


async def _sandbox_amount_for_usage(runtime, telegram_id: int, idempotency_key: str) -> int | None:
    async with runtime.database.sessions() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            return None
        row = await session.scalar(
            select(SandboxTransaction).where(
                SandboxTransaction.user_id == user.id,
                SandboxTransaction.idempotency_key == idempotency_key,
            )
        )
        return row.amount_minor if row else None


async def _show_duplicate_sandbox(
    update: Update, runtime, telegram_id: int, usage_id: int, status: str
) -> None:
    pending = await runtime.credits.get_pending_operation(telegram_id, usage_id, "stripe_sandbox")
    if status == "completed" and pending is not None:
        async with runtime.database.sessions() as session:
            row = await session.scalar(
                select(SandboxTransaction).where(
                    SandboxTransaction.idempotency_key == pending.idempotency_key
                )
            )
        if row:
            await reply_or_edit(
                update,
                f"{BRAND}\n<b>🧪 PREVIOUS SANDBOX RESULT</b>\n{SEPARATOR}\n"
                f"Status: <b>{escape_html(row.status)}</b>\n"
                f"Reference: <code>{escape_html(row.provider_ref or 'pending')}</code>\n"
                "This duplicate callback was not submitted again.",
                back_menu("nav:gates"),
            )
            return
    if status == "indeterminate":
        await reply_or_edit(
            update,
            error_message(
                "Request pending", "Use Retry to verify the same idempotent test request."
            ),
            auth_menu(True, usage_id),
        )
    else:
        await reply_or_edit(
            update,
            error_message(
                "Duplicate request", "This callback was already processed and will not run again."
            ),
            back_menu("nav:gates"),
        )


def _outcome_line(outcome: str, status: str) -> str:
    safe_status = escape_html(status)
    if outcome == "sandbox_approved":
        return "✅ Provider status: <b>SUCCEEDED</b> — test mode only"
    if outcome == "sandbox_declined":
        return "❌ Provider status: <b>DECLINED</b> — test mode only"
    if outcome == "sandbox_not_completed":
        return f"⚠️ Provider status: <b>{safe_status}</b> — not an approval"
    return f"⏳ Provider status: <b>{safe_status}</b> — pending"


def _request_key(telegram_id: int, update: Update) -> str:
    unique = f"{telegram_id}:{update.update_id}:{update.callback_query.id if update.callback_query else 'message'}"
    return hashlib.sha256(unique.encode("utf-8")).hexdigest()


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
