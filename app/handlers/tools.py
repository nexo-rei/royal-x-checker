from __future__ import annotations

import asyncio
import hashlib
import html
import logging
from datetime import UTC, datetime, timedelta

from telegram import Update
from telegram.ext import ContextTypes

from app.handlers.common import (
    acknowledge,
    check_active,
    clear_pending,
    get_runtime,
    rate_allowed,
    reply_or_edit,
    telegram_user_id,
)
from app.keyboards.menus import back_menu, fraud_menu, synthetic_menu
from app.services.errors import FileValidationError, ServiceError
from app.services.file_tools import (
    FileAnalysis,
    analyze_synthetic_csv,
    export_analysis_csv,
)
from app.ui import BRAND, SEPARATOR, error_message
from app.utils.telegram_files import send_temporary_document

logger = logging.getLogger(__name__)


def _key(update: Update, telegram_id: int, feature: str) -> str:
    return hashlib.sha256(f"{telegram_id}:{feature}:{update.update_id}".encode()).hexdigest()


async def tools_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await acknowledge(update)
    clear_pending(context)
    runtime = get_runtime(context)
    if not await check_active(update, runtime):
        return
    telegram_id = telegram_user_id(update)
    data = update.callback_query.data if update.callback_query else ""
    if telegram_id is None:
        return

    if data == "tool:bin":
        if not await runtime.features.is_enabled("bin_lookup"):
            await reply_or_edit(
                update, error_message("Tool disabled", "BIN metadata lookup is disabled.")
            )
            return
        context.user_data["pending_action"] = "bin_lookup"
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>🔎 BIN / IIN LOOKUP</b>\n{SEPARATOR}\n"
            "Send exactly 6 or 8 leading digits. Never send a full card number or security code.\n"
            "Issuer metadata may be missing or uncertain; no card activity or authorization is checked.",
            back_menu("nav:tools"),
        )
        return

    if data == "tool:synthetic":
        if not await runtime.features.is_enabled("synthetic_data"):
            await reply_or_edit(
                update, error_message("Tool disabled", "Synthetic data generation is disabled.")
            )
            return
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>🧬 SYNTHETIC DATA</b>\n{SEPARATOR}\n"
            f"Generate up to {runtime.settings.max_test_count} clearly labelled fictional test rows. "
            "No complete payment credentials are generated.",
            synthetic_menu(runtime.settings.max_test_count),
        )
        return

    if data == "tool:file":
        if not await runtime.features.is_enabled("file_analysis"):
            await reply_or_edit(
                update, error_message("Tool disabled", "File analysis is disabled.")
            )
            return
        context.user_data["pending_action"] = "file_import"
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>📂 FILE ANALYZER</b>\n{SEPARATOR}\n"
            "Upload a UTF-8 CSV labelled as synthetic test data.\n"
            f"Maximum: {runtime.settings.max_file_size_bytes:,} bytes and {runtime.settings.max_file_rows:,} rows.\n"
            "Only synthetic transaction metadata is accepted. Files are processed in memory; the exported report is temporary and cleaned up.",
            back_menu("nav:tools"),
        )
        return

    if data == "tool:fraud":
        if not await runtime.features.is_enabled("fraud_analysis"):
            await reply_or_edit(
                update, error_message("Tool disabled", "Fraud analytics is disabled.")
            )
            return
        await reply_or_edit(
            update,
            f"{BRAND}\n<b>📊 FRAUD ANALYTICS</b>\n{SEPARATOR}\n"
            "Analyze a synthetic demo dataset or a validated synthetic CSV.\n"
            "Rule-based heuristic only; not a guaranteed fraud determination.",
            fraud_menu(),
        )
        return

    if data == "tool:fraud:demo":
        await _run_fraud_demo(update, context)
        return

    if data == "tool:synthetic:profile":
        await _generate_profile(update, context)
        return

    if data.startswith("tool:synthetic:"):
        try:
            count = int(data.rsplit(":", 1)[1])
        except ValueError:
            await reply_or_edit(
                update, error_message("Invalid count", "Choose one of the displayed record counts.")
            )
            return
        await _generate_rows(update, context, count)


async def _generate_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = get_runtime(context)
    telegram_id = telegram_user_id(update)
    if telegram_id is None or not await rate_allowed(update, runtime):
        return
    key = _key(update, telegram_id, "synthetic_data")
    try:
        reservation = await runtime.credits.reserve_operation(
            telegram_id=telegram_id,
            feature="synthetic_data",
            idempotency_key=key,
            cost=await runtime.credits.feature_cost("synthetic_data"),
        )
        if reservation.duplicate:
            await reply_or_edit(
                update,
                error_message(
                    "Already processed", "This profile request has already been processed."
                ),
                back_menu("nav:tools"),
            )
            return
        profile = runtime.synthetic.generate_profile()
        await runtime.credits.complete_operation(
            reservation.usage_id, {"kind": "fictional_profile"}
        )
    except ServiceError as exc:
        await reply_or_edit(update, error_message("Generation unavailable", exc.public_message))
        return
    except Exception as exc:
        logger.error("Synthetic profile generation failed (%s)", type(exc).__name__)
        await reply_or_edit(
            update,
            error_message(
                "Generation unavailable", "The synthetic profile could not be generated."
            ),
        )
        return
    details = "\n".join(
        f"<b>{html.escape(key.title())}:</b> <code>{html.escape(str(value))}</code>"
        for key, value in profile.items()
    )
    await reply_or_edit(
        update,
        f"{BRAND}\n<b>🧬 FICTIONAL PROFILE</b>\n{SEPARATOR}\n{details}\n\n"
        "This is synthetic test data—not a real person.",
        back_menu("nav:tools"),
    )


async def _generate_rows(update: Update, context: ContextTypes.DEFAULT_TYPE, count: int) -> None:
    runtime = get_runtime(context)
    telegram_id = telegram_user_id(update)
    if telegram_id is None or not await rate_allowed(update, runtime):
        return
    if not 1 <= count <= runtime.settings.max_test_count:
        await reply_or_edit(
            update,
            error_message(
                "Invalid count", f"Count must be between 1 and {runtime.settings.max_test_count}."
            ),
        )
        return
    key = _key(update, telegram_id, "synthetic_data")
    reservation = None
    try:
        reservation = await runtime.credits.reserve_operation(
            telegram_id=telegram_id,
            feature="synthetic_data",
            idempotency_key=key,
            cost=await runtime.credits.feature_cost("synthetic_data"),
        )
        if reservation.duplicate:
            await reply_or_edit(
                update,
                error_message(
                    "Already processed",
                    "This callback has already run; no duplicate dataset was generated.",
                ),
                back_menu("nav:tools"),
            )
            return
        rows = runtime.synthetic.generate_transactions(count, runtime.settings.max_test_count)
        findings = runtime.fraud.analyze(rows)
        report = export_analysis_csv(FileAnalysis(rows, findings, 0))
        await runtime.credits.complete_operation(
            reservation.usage_id, {"kind": "synthetic_csv", "rows": count}
        )
    except ServiceError as exc:
        await reply_or_edit(update, error_message("Generation unavailable", exc.public_message))
        return
    except Exception as exc:
        if reservation:
            await runtime.credits.fail_operation(
                reservation.usage_id, refund=True, reason_code=type(exc).__name__
            )
        logger.error("Synthetic dataset generation failed (%s)", type(exc).__name__)
        await reply_or_edit(
            update,
            error_message("Generation unavailable", "The test dataset could not be generated."),
        )
        return
    message = update.effective_message
    if message is None and update.callback_query:
        message = update.callback_query.message
    if message is None:
        return
    await reply_or_edit(
        update,
        f"{BRAND}\n<b>✅ SYNTHETIC DATA READY</b>\n{SEPARATOR}\n"
        f"Generated {count} fictional transaction rows. Every row is labelled synthetic; no payment credentials are present.",
        back_menu("nav:tools"),
    )
    await send_temporary_document(
        message,
        report,
        "royal_x_synthetic_test_data.csv",
        "Synthetic test data — fictional and safe for software testing.",
    )


async def _run_fraud_demo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = get_runtime(context)
    telegram_id = telegram_user_id(update)
    if telegram_id is None or not await rate_allowed(update, runtime):
        return
    key = _key(update, telegram_id, "fraud_analysis")
    reservation = None
    try:
        reservation = await runtime.credits.reserve_operation(
            telegram_id=telegram_id,
            feature="fraud_analysis",
            idempotency_key=key,
            cost=await runtime.credits.feature_cost("fraud_analysis"),
        )
        if reservation.duplicate:
            await reply_or_edit(
                update,
                error_message(
                    "Already processed", "This analysis callback has already been processed."
                ),
                back_menu("nav:tools"),
            )
            return
        rows = runtime.synthetic.generate_transactions(10, runtime.settings.max_test_count)
        demo_time = datetime.now(UTC)
        for index, row in enumerate(rows):
            row["customer_id"] = "SYN-CUST-DEMO"
            row["amount"] = "1500.00" if index in {0, 1} else "25.00"
            row["attempt_count"] = 4 if index == 2 else 1
            row["location"] = "TEST-NORTH" if index % 2 == 0 else "TEST-SOUTH"
            row["timestamp"] = (demo_time + timedelta(minutes=index)).isoformat()
        findings = runtime.fraud.analyze(rows)
        report = export_analysis_csv(FileAnalysis(rows, findings, 0))
        await runtime.credits.complete_operation(
            reservation.usage_id, {"kind": "synthetic_fraud_demo", "rows": len(rows)}
        )
    except ServiceError as exc:
        await reply_or_edit(update, error_message("Analysis unavailable", exc.public_message))
        return
    except Exception as exc:
        if reservation:
            await runtime.credits.fail_operation(
                reservation.usage_id, refund=True, reason_code=type(exc).__name__
            )
        logger.error("Synthetic fraud analysis failed (%s)", type(exc).__name__)
        await reply_or_edit(
            update,
            error_message("Analysis unavailable", "The demo analysis could not be completed."),
        )
        return
    summary = _analysis_summary(FileAnalysis(rows, findings, 0))
    message = update.effective_message or (
        update.callback_query.message if update.callback_query else None
    )
    await reply_or_edit(
        update,
        f"{BRAND}\n<b>📊 SYNTHETIC FRAUD ANALYSIS</b>\n{SEPARATOR}\n{summary}\n\n"
        "Heuristic output only—not a guaranteed fraud determination. All input rows are generated test data.",
        fraud_menu(),
    )
    if message:
        await send_temporary_document(
            message,
            report,
            "royal_x_fraud_demo.csv",
            "Rule-based heuristic output for synthetic records.",
        )


async def file_upload_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = get_runtime(context)
    message = update.effective_message
    telegram_user = update.effective_user
    document = message.document if message else None
    if message is None or telegram_user is None or document is None:
        return
    if context.user_data.get("pending_action") != "file_import":
        await message.reply_text("Open Tools → File Analyzer before uploading a synthetic CSV.")
        return
    context.user_data.pop("pending_action", None)
    if not await check_active(update, runtime):
        return
    if not await runtime.features.is_enabled("file_analysis"):
        await message.reply_text(error_message("Tool disabled", "File analysis is disabled."))
        return
    if document.file_size is not None and document.file_size > runtime.settings.max_file_size_bytes:
        await message.reply_text(
            error_message(
                "File too large", f"Maximum size is {runtime.settings.max_file_size_bytes:,} bytes."
            )
        )
        return
    if not await rate_allowed(update, runtime):
        return

    key = _key(update, telegram_user.id, "file_analysis")
    reservation = None
    try:
        reservation = await runtime.credits.reserve_operation(
            telegram_id=telegram_user.id,
            feature="file_analysis",
            idempotency_key=key,
            cost=await runtime.credits.feature_cost("file_analysis"),
        )
        if reservation.duplicate:
            await message.reply_text(
                error_message("Already processed", "This upload update has already been analyzed.")
            )
            return
        telegram_file = await context.bot.get_file(document.file_id)
        content = bytes(await telegram_file.download_as_bytearray())
        if len(content) > runtime.settings.max_file_size_bytes:
            raise FileValidationError(
                f"Maximum size is {runtime.settings.max_file_size_bytes:,} bytes."
            )
        analysis = await asyncio.to_thread(
            analyze_synthetic_csv,
            document.file_name or "upload.csv",
            content,
            max_bytes=runtime.settings.max_file_size_bytes,
            max_rows=runtime.settings.max_file_rows,
            fraud_detector=runtime.fraud,
        )
        report = export_analysis_csv(analysis)
        await runtime.credits.complete_operation(
            reservation.usage_id,
            {
                "kind": "synthetic_file_analysis",
                "rows": len(analysis.rows),
                "duplicates": analysis.duplicate_count,
            },
        )
    except (ServiceError, FileValidationError) as exc:
        if reservation:
            await runtime.credits.fail_operation(
                reservation.usage_id, refund=True, reason_code=type(exc).__name__
            )
        await message.reply_text(error_message("File rejected", exc.public_message))
        return
    except Exception as exc:
        if reservation:
            await runtime.credits.fail_operation(
                reservation.usage_id, refund=True, reason_code=type(exc).__name__
            )
        logger.error("File analysis failed safely (%s)", type(exc).__name__)
        await message.reply_text(
            error_message(
                "File analysis failed", "The file could not be processed. No result was stored."
            )
        )
        return

    await message.reply_text(
        f"{BRAND}\n<b>✅ SYNTHETIC FILE ANALYSIS</b>\n{SEPARATOR}\n"
        f"{_analysis_summary(analysis)}\n\n"
        "This is a configurable heuristic, not a guaranteed fraud determination. Uploaded content was processed in memory and is not retained."
    )
    await send_temporary_document(
        message,
        report,
        "royal_x_analysis_report.csv",
        "Safe CSV report; temporary output is cleaned after upload.",
    )


def _analysis_summary(analysis: FileAnalysis) -> str:
    low = sum(finding.level == "low" for finding in analysis.findings)
    medium = sum(finding.level == "medium" for finding in analysis.findings)
    high = sum(finding.level == "high" for finding in analysis.findings)
    rules = sorted({rule for finding in analysis.findings for rule in finding.rules})
    rule_lines = ", ".join(html.escape(rule.replace("_", " ")) for rule in rules) or "None"
    return (
        f"Rows analyzed: <b>{len(analysis.rows)}</b>\n"
        f"Duplicates: <b>{analysis.duplicate_count}</b>\n"
        f"Risk summary: low {low} · medium {medium} · high {high}\n"
        f"Triggered rules: {rule_lines}"
    )


async def tools_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app.handlers.menu import tools_command as show_tools

    await show_tools(update, context)
