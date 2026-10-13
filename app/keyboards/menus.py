from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def _markup(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text, callback_data=data) for text, data in row] for row in rows]
    )


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [("👑 GATES", "nav:gates"), ("🛠️ TOOLS", "nav:tools")],
        [("💎 PLANS", "nav:plans"), ("👤 PROFILE", "nav:profile")],
        [("📩 FEEDBACK", "nav:feedback"), ("ℹ️ HELP", "nav:help")],
    ]
    if is_admin:
        rows.append([("👑 OWNER PANEL", "nav:admin")])
    return _markup(rows)


def gates_menu() -> InlineKeyboardMarkup:
    return _markup(
        [
            [("🧪 AUTH — Sandbox Testing", "gate:auth")],
            [("💳 CHARGE — Local Simulator", "gate:sim")],
            [("🛡️ FRAUD LAB", "gate:fraud")],
            [("📜 TEST HISTORY", "gate:history")],
            [("🏠 Main Menu", "nav:main")],
        ]
    )


def tools_menu(enabled: set[str]) -> InlineKeyboardMarkup:
    rows: list[list[tuple[str, str]]] = []
    if "bin_lookup" in enabled:
        rows.append([("🔎 BIN / IIN LOOKUP", "tool:bin")])
    if "synthetic_data" in enabled:
        rows.append([("🧬 SYNTHETIC DATA", "tool:synthetic")])
    if "file_analysis" in enabled:
        rows.append([("📂 FILE ANALYZER", "tool:file")])
    if "fraud_analysis" in enabled:
        rows.append([("📊 FRAUD ANALYTICS", "tool:fraud")])
    rows.extend(
        [
            [("📩 FEEDBACK", "nav:feedback")],
            [("🔙 Back", "nav:main")],
        ]
    )
    return _markup(rows)


def back_menu(destination: str = "nav:main", include_main: bool = False) -> InlineKeyboardMarkup:
    rows = [[("🔙 Back", destination)]]
    if include_main:
        rows.append([("🏠 Main Menu", "nav:main")])
    return _markup(rows)


def profile_menu() -> InlineKeyboardMarkup:
    return _markup(
        [
            [("💎 View Plans", "nav:plans")],
            [("🔄 Refresh", "nav:profile")],
            [("🏠 Main Menu", "nav:main")],
        ]
    )


def plan_menu(plans: list[tuple[str, str, bool]]) -> InlineKeyboardMarkup:
    rows = []
    for code, name, selectable in plans:
        if selectable:
            rows.append([(f"💎 Select {name}", f"plan:req:{code}")])
    rows.extend([[("👤 Profile", "nav:profile")], [("🏠 Main Menu", "nav:main")]])
    return _markup(rows)


def auth_menu(provider_ready: bool, pending_id: int | None = None) -> InlineKeyboardMarkup:
    rows: list[list[tuple[str, str]]] = []
    if provider_ready:
        rows.extend(
            [
                [("🧪 Run $1.00 Stripe test", "auth:stripe:100")],
                [("🧪 Run $5.00 Stripe test", "auth:stripe:500")],
            ]
        )
    if pending_id is not None:
        rows.append([("🔄 Retry same sandbox request", f"auth:retry:{pending_id}")])
    rows.append([("🔙 Back to Gates", "nav:gates")])
    return _markup(rows)


def simulation_menu(max_count: int = 20) -> InlineKeyboardMarkup:
    batch_counts = [count for count in (1, 5, 10, 20) if count <= max_count]
    rows: list[list[tuple[str, str]]] = []
    if batch_counts:
        rows.append(
            [(f"🎲 Auto batch · {count}", f"sim:batch:{count}") for count in batch_counts[:2]]
        )
    if len(batch_counts) > 2:
        rows.append([(f"Auto batch · {count}", f"sim:batch:{count}") for count in batch_counts[2:]])
    rows.extend(
        [
            [("🎲 Random Scenario", "sim:scenario:random")],
            [
                ("Routine", "sim:scenario:routine"),
                ("Unusual Amount", "sim:scenario:unusual_amount"),
            ],
            [("Repeated Attempts", "sim:scenario:repeated_attempts")],
            [("Rapid Location Change", "sim:scenario:location_change")],
            [("🔙 Back to Gates", "nav:gates")],
        ]
    )
    return _markup(rows)


def fraud_menu() -> InlineKeyboardMarkup:
    return _markup(
        [
            [("📊 Analyze synthetic sample", "tool:fraud:demo")],
            [("📂 Analyze a synthetic CSV", "tool:file")],
            [("🔙 Back to Tools", "nav:tools")],
        ]
    )


def synthetic_menu(max_count: int) -> InlineKeyboardMarkup:
    counts = [count for count in (1, 5, 10, 20) if count <= max_count]
    rows = [[(f"Generate {count} test rows", f"tool:synthetic:{count}") for count in counts[:2]]]
    if len(counts) > 2:
        rows.append([(f"Generate {count}", f"tool:synthetic:{count}") for count in counts[2:]])
    rows.append([("Generate fictional profile", "tool:synthetic:profile")])
    rows.append([("🔙 Back to Tools", "nav:tools")])
    return _markup(rows)


def owner_menu() -> InlineKeyboardMarkup:
    return _markup(
        [
            [("📊 Statistics", "admin:stats"), ("👥 User Management", "admin:users")],
            [("💎 Plan Management", "admin:plans"), ("⚙️ Settings", "admin:settings")],
            [("🧾 Sandbox Summary", "admin:sandbox"), ("📋 Audit Logs", "admin:audit")],
            [("📩 Feedback Queue", "admin:feedback"), ("🧾 Subscription Queue", "admin:subs")],
            [("🏠 Main Menu", "nav:main")],
        ]
    )


def admin_back() -> InlineKeyboardMarkup:
    return _markup([[("🔙 Owner Panel", "nav:admin")], [("🏠 Main Menu", "nav:main")]])


def confirm_admin(action: str, cancel_to: str = "admin:open") -> InlineKeyboardMarkup:
    return _markup(
        [
            [("✅ Confirm", action)],
            [("❌ Cancel", cancel_to)],
        ]
    )


def admin_features(features: dict[str, bool]) -> InlineKeyboardMarkup:
    labels = {
        "stripe_sandbox": "Stripe sandbox",
        "bin_lookup": "BIN lookup",
        "file_analysis": "File analyzer",
        "simulation": "Simulator",
        "synthetic_data": "Synthetic data",
        "fraud_analysis": "Fraud analytics",
        "feedback": "Feedback",
    }
    rows: list[list[tuple[str, str]]] = []
    for feature, enabled in features.items():
        state = "ON" if enabled else "OFF"
        rows.append([(f"{state} · {labels.get(feature, feature)}", f"admin:feature:ask:{feature}")])
    rows.extend(
        [
            [("💳 Configure operation costs", "admin:costs")],
            [("📊 Configure plan quotas", "admin:quotas")],
            [("🔙 Owner Panel", "nav:admin")],
        ]
    )
    return _markup(rows)


def admin_cost_options(costs: dict[str, int]) -> InlineKeyboardMarkup:
    rows = [
        [(f"{feature.replace('_', ' ').title()} · {cost} credits", f"admin:cost:{feature}")]
        for feature, cost in costs.items()
    ]
    rows.append([("🔙 Settings", "admin:settings")])
    return _markup(rows)


def admin_plan_list(plans: list[tuple[str, str, bool]]) -> InlineKeyboardMarkup:
    rows = [
        [(f"{'ON' if enabled else 'OFF'} · {name}", f"admin:plan:view:{code}")]
        for code, name, enabled in plans
    ]
    rows.append([("🔙 Owner Panel", "nav:admin")])
    return _markup(rows)


def admin_plan_actions(code: str, enabled: bool) -> InlineKeyboardMarkup:
    rows: list[list[tuple[str, str]]] = []
    if code != "free":
        rows.append([("⚙️ Change price", f"admin:price:{code}")])
    rows.append([("📊 Change daily quota", f"admin:quota:{code}")])
    if code != "free":
        rows.append(
            [("⛔ Disable plan" if enabled else "✅ Enable plan", f"admin:plan-toggle:ask:{code}")]
        )
    rows.append([("🔙 Plan Management", "admin:plans")])
    return _markup(rows)


def admin_user_actions(telegram_id: int, active: bool) -> InlineKeyboardMarkup:
    status_action = "suspend" if active else "activate"
    status_label = "⛔ Suspend account" if active else "✅ Reactivate account"
    return _markup(
        [
            [("➕ Add development credits", f"admin:credit:{telegram_id}")],
            [(status_label, f"admin:user-status:ask:{status_action}:{telegram_id}")],
            [("🔙 User Management", "admin:users")],
        ]
    )


def feedback_queue(items: list[tuple[int, int]]) -> InlineKeyboardMarkup:
    rows = [
        [(f"📩 Feedback #{feedback_id} · user {user_id}", f"admin:feedback:view:{feedback_id}")]
        for feedback_id, user_id in items
    ]
    rows.extend([[("🔙 Owner Panel", "nav:admin")]])
    return _markup(rows)


def feedback_actions(feedback_id: int) -> InlineKeyboardMarkup:
    return _markup(
        [
            [("✅ Mark reviewed", f"admin:feedback:resolve:{feedback_id}")],
            [("🔙 Feedback Queue", "admin:feedback")],
        ]
    )


def subscription_queue(items: list[tuple[int, str, str]]) -> InlineKeyboardMarkup:
    rows = [
        [(f"🧾 #{sub_id} · {plan_name} · user {user_id}", f"admin:sub:view:{sub_id}")]
        for sub_id, user_id, plan_name in items
    ]
    rows.append([("🔙 Owner Panel", "nav:admin")])
    return _markup(rows)


def subscription_actions(subscription_id: int) -> InlineKeyboardMarkup:
    return _markup(
        [
            [("✅ Approve activation", f"admin:sub:approve:ask:{subscription_id}")],
            [("❌ Reject request", f"admin:sub:reject:ask:{subscription_id}")],
            [("🔙 Subscription Queue", "admin:subs")],
        ]
    )
