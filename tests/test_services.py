from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.database.models import CreditLedger, SandboxTransaction, UsageRecord
from app.services.errors import InsufficientCredits, QuotaExceeded
from app.services.fraud_detection import FraudDetectionService
from app.services.simulator import TransactionSimulator
from app.services.synthetic_data import SyntheticDataGenerator


def test_synthetic_generator_labels_data_and_enforces_limits() -> None:
    generator = SyntheticDataGenerator()
    profile = generator.generate_profile()
    assert profile["email"].endswith("@example.invalid")
    assert "SYNTHETIC" in profile["synthetic_label"]
    rows = generator.generate_transactions(3, max_count=5)
    assert len(rows) == 3
    assert all(row["synthetic_label"] == "synthetic" for row in rows)
    assert all("card" not in row and "cvv" not in row for row in rows)
    with pytest.raises(ValueError):
        generator.generate_transactions(6, max_count=5)


def test_local_simulator_returns_configured_synthetic_categories() -> None:
    scenarios = {"routine", "unusual_amount", "repeated_attempts", "location_change"}
    rules = {
        scenario: {"approved": 0.0, "declined": 0.0, "suspicious": 0.0, "simulated_error": 1.0}
        for scenario in scenarios
    }
    simulator = TransactionSimulator(rules, max_amount_minor=100_000)
    result = simulator.simulate("random", "12.50")
    assert result.scenario in scenarios
    assert result.outcome == "simulated_error"
    assert result.amount_minor == 1_250
    assert "no provider" in result.explanation.lower()
    batch = simulator.simulate_batch(3, max_count=5)
    assert len(batch) == 3
    assert all(item.outcome == "simulated_error" for item in batch)
    assert b"SYN-MERCHANT-TEST" in simulator.export_batch_csv(batch)
    with pytest.raises(ValueError):
        simulator.simulate_batch(6, max_count=5)
    with pytest.raises(ValueError):
        simulator.simulate("routine", "0")
    with pytest.raises(ValueError, match="two decimal places"):
        simulator.simulate("routine", "1.001")


def test_fraud_heuristics_explain_rules_and_cap_score() -> None:
    detector = FraudDetectionService(
        high_amount_threshold=1_000,
        repeated_attempt_threshold=3,
        frequency_threshold=3,
        location_change_window_minutes=30,
    )
    base = datetime.now(UTC)
    records = [
        {
            "customer_id": "SYN-1",
            "amount": "1500.00",
            "attempt_count": 4,
            "location": "TEST-A" if index % 2 == 0 else "TEST-B",
            "timestamp": (base + timedelta(minutes=index)).isoformat(),
        }
        for index in range(4)
    ]
    findings = detector.analyze(records)
    assert findings[0].level == "high"
    assert findings[0].score <= 100
    assert "unusual_amount" in findings[0].rules
    assert "repeated_attempts" in findings[0].rules
    assert "high_transaction_frequency" in findings[0].rules
    assert "rapid_location_change" in findings[1].rules
    assert "Triggered" in findings[0].explanation


async def test_registration_credit_debit_and_idempotency(runtime) -> None:
    user = await runtime.users.ensure_registered(234, "A Test User")
    assert user.credits == runtime.settings.free_initial_credits
    first = await runtime.credits.reserve_operation(
        telegram_id=234,
        feature="stripe_sandbox",
        idempotency_key="operation-test-1",
        cost=2,
    )
    assert first.balance_after == 3
    await runtime.credits.complete_operation(first.usage_id, {"status": "test"})
    duplicate = await runtime.credits.reserve_operation(
        telegram_id=234,
        feature="stripe_sandbox",
        idempotency_key="operation-test-1",
        cost=2,
    )
    profile = await runtime.users.profile(234)
    assert duplicate.duplicate is True
    assert duplicate.status == "completed"
    assert profile is not None and profile.credits == 3

    async with runtime.database.sessions() as session:
        ledger_count = int(await session.scalar(select(func.count(CreditLedger.id))) or 0)
        usage_count = int(await session.scalar(select(func.count(UsageRecord.id))) or 0)
    assert ledger_count == 2  # Initial free grant + one debit.
    assert usage_count == 1


async def test_concurrent_credit_deductions_cannot_overdraw(runtime) -> None:
    await runtime.users.ensure_registered(345, "Concurrent User")

    async def run(key: str):
        try:
            return await runtime.credits.reserve_operation(
                telegram_id=345,
                feature="stripe_sandbox",
                idempotency_key=key,
                cost=4,
            )
        except InsufficientCredits:
            return "insufficient"

    results = await asyncio.gather(run("concurrent-a"), run("concurrent-b"))
    assert sum(result != "insufficient" for result in results) == 1
    profile = await runtime.users.profile(345)
    assert profile is not None and profile.credits == 1
    async with runtime.database.sessions() as session:
        row = await session.scalar(select(UsageRecord).where(UsageRecord.user_id == 1))
        user_balance = await session.scalar(select(func.min(CreditLedger.balance_after)))
    assert row is not None
    assert user_balance >= 1


async def test_failed_operation_refunds_and_frees_daily_quota(runtime) -> None:
    await runtime.users.ensure_registered(456, "Quota User")
    await runtime.plans.update_plan("free", admin_id=9001, usage_quota=1)
    reserved = await runtime.credits.reserve_operation(
        telegram_id=456,
        feature="stripe_sandbox",
        idempotency_key="will-fail",
        cost=2,
    )
    await runtime.credits.fail_operation(reserved.usage_id, refund=True, reason_code="mock_failure")
    next_op = await runtime.credits.reserve_operation(
        telegram_id=456,
        feature="simulation",
        idempotency_key="will-run",
        cost=0,
    )
    await runtime.credits.complete_operation(next_op.usage_id)
    with pytest.raises(QuotaExceeded):
        await runtime.credits.reserve_operation(
            telegram_id=456,
            feature="simulation",
            idempotency_key="over-limit",
            cost=0,
        )
    profile = await runtime.users.profile(456)
    assert profile is not None
    assert profile.credits == runtime.settings.free_initial_credits
    assert profile.usage_today == 1


async def test_manual_plan_activation_is_admin_reviewed(runtime) -> None:
    await runtime.users.ensure_registered(567, "Plan Requester")
    subscription, created = await runtime.plans.request_activation(
        567, "basic", "subscription-request-key"
    )
    assert created is True
    _, duplicate_created = await runtime.plans.request_activation(
        567, "premium", "subscription-request-key-2"
    )
    assert duplicate_created is False
    await runtime.plans.review_subscription(subscription.id, 9001, approve=True)
    profile = await runtime.users.profile(567)
    assert profile is not None and profile.plan_code == "basic"


async def test_restart_recovery_refunds_local_work_and_preserves_stripe_idempotency(
    runtime,
) -> None:
    await runtime.users.ensure_registered(678, "Recovery User")
    stripe = await runtime.credits.reserve_operation(
        telegram_id=678,
        feature="stripe_sandbox",
        idempotency_key="stripe-recovery-key",
        cost=2,
    )
    local = await runtime.credits.reserve_operation(
        telegram_id=678,
        feature="file_analysis",
        idempotency_key="local-recovery-key",
        cost=1,
    )
    async with runtime.database.sessions.begin() as session:
        from sqlalchemy import select

        from app.database.models import User

        user = await session.scalar(select(User).where(User.telegram_id == 678))
        assert user is not None
        session.add(
            SandboxTransaction(
                user_id=user.id,
                usage_id=stripe.usage_id,
                provider="stripe_test_mode",
                provider_ref=None,
                status="processing",
                outcome="pending",
                amount_minor=100,
                currency="USD",
                test_mode=True,
                idempotency_key=stripe.idempotency_key,
                safe_metadata={"test_mode": True},
            )
        )
    assert local.status == "processing"
    recovered = await runtime.credits.recover_stale_operations(older_than_seconds=0)
    assert recovered == 2
    stripe_pending = await runtime.credits.get_pending_operation(
        678, stripe.usage_id, "stripe_sandbox"
    )
    assert stripe_pending is not None and stripe_pending.status == "indeterminate"
    local_pending = await runtime.credits.get_pending_operation(
        678, local.usage_id, "file_analysis"
    )
    assert local_pending is not None and local_pending.status == "failed"
    resumed = await runtime.credits.resume_indeterminate(
        telegram_id=678,
        usage_id=stripe.usage_id,
        feature="stripe_sandbox",
    )
    assert resumed.idempotency_key == stripe.idempotency_key
    profile = await runtime.users.profile(678)
    assert profile is not None and profile.credits == runtime.settings.free_initial_credits - 2
