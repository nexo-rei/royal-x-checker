from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.integrations.binlist import BinlistProvider
from app.integrations.stripe_sandbox import StripeSandboxProvider
from app.services.bin_lookup import BinLookupService
from app.services.errors import (
    IntegrationNotConfigured,
    InvalidInput,
    NotFound,
    ProviderFailure,
    ProviderIndeterminate,
    SandboxDisabled,
)


async def test_bin_metadata_lookup_is_prefix_only_and_cached() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/123456"
        return httpx.Response(
            200,
            json={
                "scheme": "visa",
                "type": "debit",
                "brand": "Example debit",
                "country": {"name": "Testland"},
                "bank": {"name": "Example Issuer"},
            },
        )

    provider = BinlistProvider(enabled=True, transport=httpx.MockTransport(handler))
    service = BinLookupService(provider, cache_seconds=60)
    result = await service.lookup("123456")
    again = await service.lookup("123456")
    assert result == again
    assert result.network == "visa"
    assert result.issuer == "Example Issuer"
    assert calls == 1


async def test_bin_provider_missing_and_network_errors_are_safe() -> None:
    async def missing(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    lookup = BinLookupService(BinlistProvider(enabled=True, transport=httpx.MockTransport(missing)))
    with pytest.raises(NotFound):
        await lookup.lookup("123456")
    with pytest.raises(InvalidInput):
        await lookup.lookup("4242424242424242")

    async def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("mock offline", request=request)

    failing = BinLookupService(
        BinlistProvider(enabled=True, transport=httpx.MockTransport(offline))
    )
    with pytest.raises(ProviderFailure):
        await failing.lookup("123456")


async def test_bin_adapter_disabled_does_not_call_provider() -> None:
    provider = BinlistProvider(enabled=False)
    with pytest.raises(IntegrationNotConfigured):
        await provider.lookup("123456")


async def test_stripe_adapter_uses_test_payment_method_and_verifies_server_status() -> None:
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers.get("Authorization", "").startswith("Basic ")
        if request.method == "POST":
            form = parse_qs(request.content.decode())
            assert form["payment_method"] == ["pm_card_visa"]
            assert form["amount"] == ["100"]
            assert form["confirm"] == ["true"]
            assert request.headers.get("Idempotency-Key", "").startswith("royalx-")
            return httpx.Response(
                200,
                json={
                    "id": "pi_test_123",
                    "amount": 100,
                    "currency": "usd",
                    "status": "processing",
                },
            )
        assert request.url.path == "/v1/payment_intents/pi_test_123"
        return httpx.Response(
            200, json={"id": "pi_test_123", "amount": 100, "currency": "usd", "status": "succeeded"}
        )

    settings = Settings(
        BOT_TOKEN="123:token",
        OWNER_IDS="42",
        STRIPE_SANDBOX_ENABLED=True,
        STRIPE_SECRET_KEY=SecretStr("sk_test_example"),
    )
    provider = StripeSandboxProvider(settings, transport=httpx.MockTransport(handler))
    result = await provider.run_test_payment(amount_minor=100, idempotency_key="test-request")
    assert len(calls) == 2
    assert result.test_mode is True
    assert result.outcome == "sandbox_approved"
    assert result.status == "succeeded"
    assert result.reference == "pi_test_123"


async def test_stripe_disabled_and_unconfigured_never_send_requests() -> None:
    disabled = StripeSandboxProvider(Settings(BOT_TOKEN="123:token", OWNER_IDS="42"))
    with pytest.raises(SandboxDisabled):
        await disabled.run_test_payment(amount_minor=100, idempotency_key="no-call")

    unconfigured = StripeSandboxProvider(
        Settings(BOT_TOKEN="123:token", OWNER_IDS="42", STRIPE_SANDBOX_ENABLED=True)
    )
    with pytest.raises(IntegrationNotConfigured):
        await unconfigured.run_test_payment(amount_minor=100, idempotency_key="no-call")


async def test_stripe_unavailable_response_is_indeterminate_and_never_approved() -> None:
    async def unavailable(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"type": "api_error"}})

    settings = Settings(
        BOT_TOKEN="123:token",
        OWNER_IDS="42",
        STRIPE_SANDBOX_ENABLED=True,
        STRIPE_SECRET_KEY="sk_test_example",
    )
    provider = StripeSandboxProvider(settings, transport=httpx.MockTransport(unavailable))
    with pytest.raises(ProviderIndeterminate):
        await provider.run_test_payment(amount_minor=100, idempotency_key="uncertain")


async def test_stripe_rejects_mismatched_verified_amount() -> None:
    async def mismatch(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={"id": "pi_test_bad", "amount": 100, "currency": "usd", "status": "succeeded"},
            )
        return httpx.Response(
            200, json={"id": "pi_test_bad", "amount": 500, "currency": "usd", "status": "succeeded"}
        )

    settings = Settings(
        BOT_TOKEN="123:token",
        OWNER_IDS="42",
        STRIPE_SANDBOX_ENABLED=True,
        STRIPE_SECRET_KEY="sk_test_example",
    )
    provider = StripeSandboxProvider(settings, transport=httpx.MockTransport(mismatch))
    with pytest.raises(ProviderFailure):
        await provider.run_test_payment(amount_minor=100, idempotency_key="mismatch")
