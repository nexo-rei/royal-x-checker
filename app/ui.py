from __future__ import annotations

import html
from datetime import UTC, datetime

from app.services.users import UserProfile

BRAND = "<b>👑 ROYAL X CHECKER</b>"
SEPARATOR = "━━━━━━━━━━━━━━━━━━"


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def utc_display(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def money(amount_minor: int, currency: str = "USD") -> str:
    return f"{currency.upper()} {amount_minor / 100:,.2f}"


def welcome_message(first_name: str, profile: UserProfile | None, is_admin: bool) -> str:
    name = escape(first_name.strip() or "there")
    if profile:
        plan_line = f"💎 Plan: <b>{escape(profile.plan_name)}</b>"
        remaining = max(0, profile.daily_limit - profile.usage_today)
        quota_line = f"📊 Daily quota remaining: <b>{remaining}/{profile.daily_limit}</b>"
    else:
        plan_line = "💎 Plan: setup required"
        quota_line = "📊 Today's quota: unavailable"
    admin_line = "\n👑 Owner controls are available." if is_admin else ""
    return (
        f"{BRAND}\n{SEPARATOR}\n<b>⚡ PAYMENT SECURITY LAB</b>\n{SEPARATOR}\n"
        f"Welcome, {name}!\n\n🛡️ Secure Testing\n🧪 Sandbox Laboratory\n📊 Transaction Analytics\n\n"
        f"{plan_line}\n{quota_line}{admin_line}\n\nChoose an option below to continue."
    )


def profile_message(profile: UserProfile) -> str:
    return (
        f"{BRAND}\n<b>👤 YOUR PROFILE</b>\n{SEPARATOR}\n"
        f"🆔 User ID: <code>{profile.telegram_id}</code>\n"
        f"💎 Plan: <b>{escape(profile.plan_name)}</b>\n"
        f"⚡ Credits: <b>{profile.credits}</b>\n"
        f"📊 Usage Today: <b>{profile.usage_today}/{profile.daily_limit}</b>\n"
        f"📅 Member Since: {utc_display(profile.member_since)}\n"
        f"🛡️ Account: <b>{escape(profile.status)}</b>"
    )


def plans_header() -> str:
    return f"{BRAND}\n<b>💎 PLANS &amp; ACCESS</b>\n{SEPARATOR}"


def error_message(title: str, detail: str) -> str:
    return f"<b>⚠️ {escape(title.upper())}</b>\n{SEPARATOR}\n{escape(detail)}"


def compact_status(title: str, detail: str) -> str:
    return f"<b>{escape(title)}</b>\n{SEPARATOR}\n{detail}"


def format_user_status(profile: UserProfile) -> str:
    return f"{escape(profile.plan_name)} · {profile.credits} credits · {profile.usage_today}/{profile.daily_limit} today"
