from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.handlers.admin import admin_callback
from app.handlers.gates import gates_callback
from app.handlers.menu import menu_callback
from app.handlers.plans import plans_callback
from app.handlers.start import start_command
from app.handlers.tools import file_upload_handler, tools_callback
from app.keyboards.menus import (
    admin_features,
    gates_menu,
    main_menu,
    owner_menu,
    plan_menu,
    profile_menu,
    simulation_menu,
    synthetic_menu,
    tools_menu,
)
from app.main import build_application


def fake_update(telegram_id: int, *, data: str | None = None, update_id: int = 1, message=None):
    telegram_user = SimpleNamespace(
        id=telegram_id, full_name=f"Test User {telegram_id}", first_name="Test"
    )
    message = message or SimpleNamespace(
        reply_text=AsyncMock(), reply_document=AsyncMock(), document=None, text=None
    )
    query = None
    if data is not None:
        query = SimpleNamespace(
            data=data,
            id=f"callback-{update_id}",
            message=message,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
    return SimpleNamespace(
        effective_user=telegram_user,
        effective_message=message,
        callback_query=query,
        update_id=update_id,
    )


def fake_context(runtime):
    return SimpleNamespace(
        application=SimpleNamespace(bot_data={"runtime": runtime}),
        user_data={},
        bot=SimpleNamespace(get_file=AsyncMock()),
    )


async def test_start_registers_user_and_shows_mobile_dashboard(runtime) -> None:
    update = fake_update(1234)
    context = fake_context(runtime)
    await start_command(update, context)
    update.effective_message.reply_text.assert_awaited_once()
    text = update.effective_message.reply_text.await_args.args[0]
    markup = update.effective_message.reply_text.await_args.kwargs["reply_markup"]
    assert "ROYAL X CHECKER" in text
    assert "Daily quota remaining" in text
    assert len(markup.inline_keyboard) >= 3
    profile = await runtime.users.profile(1234)
    assert profile is not None


async def test_navigation_callbacks_and_each_main_section_respond(runtime) -> None:
    context = fake_context(runtime)
    for index, data in enumerate(
        (
            "nav:main",
            "nav:gates",
            "nav:tools",
            "nav:plans",
            "nav:profile",
            "nav:feedback",
            "nav:help",
            "nav:admin",
        ),
        start=1,
    ):
        update = fake_update(9001, data=data, update_id=index)
        await menu_callback(update, context)
        update.callback_query.answer.assert_awaited_once()
        update.callback_query.edit_message_text.assert_awaited_once()
    assert context.user_data.get("pending_action") is None


async def test_navigation_callbacks_clear_pending_prompts(runtime) -> None:
    context = fake_context(runtime)

    feedback = fake_update(1234, data="nav:feedback", update_id=15)
    await menu_callback(feedback, context)
    assert context.user_data.get("pending_action") == "feedback"
    await menu_callback(fake_update(1234, data="nav:main", update_id=16), context)
    assert "pending_action" not in context.user_data

    await gates_callback(fake_update(1234, data="sim:scenario:routine", update_id=17), context)
    assert context.user_data.get("pending_action") == "simulation_amount"
    await gates_callback(fake_update(1234, data="nav:gates", update_id=18), context)
    assert "pending_action" not in context.user_data

    await tools_callback(fake_update(1234, data="tool:file", update_id=19), context)
    assert context.user_data.get("pending_action") == "file_import"
    await tools_callback(fake_update(1234, data="nav:tools", update_id=20), context)
    assert "pending_action" not in context.user_data


async def test_gate_callbacks_have_real_handlers(runtime) -> None:
    context = fake_context(runtime)
    for index, data in enumerate(
        (
            "gate:auth",
            "gate:sim",
            "sim:scenario:routine",
            "sim:batch:2",
            "gate:fraud",
            "gate:history",
            "auth:stripe:100",
        ),
        start=20,
    ):
        update = fake_update(1234, data=data, update_id=index)
        await gates_callback(update, context)
        update.callback_query.answer.assert_awaited_once()
        update.callback_query.edit_message_text.assert_awaited()
    assert context.user_data.get("pending_action") is None


async def test_tools_callbacks_generate_safe_outputs_and_prompts(runtime) -> None:
    context = fake_context(runtime)
    for index, data in enumerate(
        (
            "tool:bin",
            "tool:synthetic",
            "tool:synthetic:profile",
            "tool:synthetic:2",
            "tool:file",
            "tool:fraud",
            "tool:fraud:demo",
        ),
        start=40,
    ):
        update = fake_update(1234, data=data, update_id=index)
        await tools_callback(update, context)
        update.callback_query.answer.assert_awaited_once()
        update.callback_query.edit_message_text.assert_awaited_once()
    assert context.user_data.get("pending_action") is None
    assert update.effective_message.reply_document.await_count == 1


async def test_plan_request_is_persisted_as_pending_not_activated(runtime) -> None:
    await runtime.users.ensure_registered(2222, "Plan User")
    update = fake_update(2222, data="plan:req:basic", update_id=55)
    await plans_callback(update, fake_context(runtime))
    update.callback_query.answer.assert_awaited_once()
    rows = await runtime.plans.pending_subscriptions()
    assert len(rows) == 1
    assert rows[0][2].code == "basic"
    profile = await runtime.users.profile(2222)
    assert profile is not None and profile.plan_code == "free"


async def test_admin_callbacks_are_authorized_in_backend(runtime) -> None:
    denied = fake_update(1234, data="admin:stats", update_id=60)
    await admin_callback(denied, fake_context(runtime))
    assert "UNAUTHORIZED" in denied.callback_query.edit_message_text.await_args.kwargs["text"]

    allowed = fake_update(9001, data="admin:stats", update_id=61)
    await admin_callback(allowed, fake_context(runtime))
    assert "ADMIN STATISTICS" in allowed.callback_query.edit_message_text.await_args.kwargs["text"]


async def test_file_upload_uses_validated_synthetic_csv(runtime) -> None:
    content = (
        b"synthetic_label,transaction_id,customer_id,amount,currency,location,attempt_count\n"
        b"synthetic,SYN-1,SYN-C1,12.50,USD,TEST-NORTH,1\n"
    )
    telegram_file = SimpleNamespace(
        download_as_bytearray=AsyncMock(return_value=bytearray(content))
    )
    message = SimpleNamespace(
        reply_text=AsyncMock(),
        reply_document=AsyncMock(),
        document=SimpleNamespace(file_id="file-id", file_size=len(content), file_name="safe.csv"),
        text=None,
    )
    update = fake_update(3333, message=message, update_id=70)
    context = fake_context(runtime)
    context.user_data["pending_action"] = "file_import"
    context.bot.get_file.return_value = telegram_file
    await file_upload_handler(update, context)
    assert message.reply_text.await_count == 1
    assert message.reply_document.await_count == 1
    profile = await runtime.users.profile(3333)
    assert profile is not None and profile.credits == runtime.settings.free_initial_credits - 1


def _callback_data(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_every_menu_button_uses_a_registered_callback_prefix(runtime) -> None:
    app = build_application(runtime.settings)
    patterns = [
        handler.pattern.pattern for handler in app.handlers[0] if getattr(handler, "pattern", None)
    ]
    markups = [
        main_menu(True),
        gates_menu(),
        tools_menu({"bin_lookup", "synthetic_data", "file_analysis", "fraud_analysis"}),
        plan_menu([("basic", "Basic", True)]),
        profile_menu(),
        simulation_menu(),
        synthetic_menu(20),
        owner_menu(),
        admin_features({"bin_lookup": True, "stripe_sandbox": False}),
    ]
    for markup in markups:
        for callback_data in _callback_data(markup):
            assert any(__import__("re").search(pattern, callback_data) for pattern in patterns), (
                callback_data
            )
