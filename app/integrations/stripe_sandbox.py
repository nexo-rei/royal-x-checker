from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings
from app.integrations.base import SandboxResult
from app.services.errors import (
    IntegrationNotConfigured,
    ProviderFailure,
    ProviderIndeterminate,
    SandboxDisabled,
)

_STRIPE_API = "https://api.stripe.com/v1"
_TEST_PAYMENT_METHOD = "pm_card_visa"


class StripeSandboxProvider:
    """Stripe PaymentIntent adapter restricted to Stripe test-mode keys and test methods.

    Docs: https://docs.stripe.com/testing and https://docs.stripe.com/api/payment_intents
    """

    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self.transport = transport

    @property
    def status(self) -> str:
        if not self.settings.stripe_sandbox_enabled:
            return "disabled"
        if self.settings.stripe_secret_key is None:
            return "not_configured"
        if not self.settings.stripe_secret_key.get_secret_value().startswith("sk_test_"):
            return "invalid_configuration"
        return "ready"

    async def run_test_payment(
        self, *, amount_minor: int, currency: str = "usd", idempotency_key: str
    ) -> SandboxResult:
        if not self.settings.stripe_sandbox_enabled:
            raise SandboxDisabled()
        if self.settings.stripe_secret_key is None:
            raise IntegrationNotConfigured()
        if self.status != "ready":
            raise IntegrationNotConfigured()
        if amount_minor < 50 or amount_minor > 10_000:
            raise ValueError("Stripe test amount must be between 0.50 and 100.00 USD.")
        if currency.lower() != "usd":
            raise ValueError("Only the configured USD sandbox test flow is supported.")

        secret = self.settings.stripe_secret_key.get_secret_value()
        provider_key = "royalx-" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        timeout = httpx.Timeout(self.settings.stripe_timeout_seconds)
        headers = {"Idempotency-Key": provider_key, "Accept": "application/json"}
        form_data = {
            "amount": str(amount_minor),
            "currency": "usd",
            "payment_method": _TEST_PAYMENT_METHOD,
            "payment_method_types[]": "card",
            "confirm": "true",
            "description": "Royal X Checker authorized sandbox test",
            "metadata[application]": "royal_x_checker",
            "metadata[operation]": "sandbox_test",
        }

        async with httpx.AsyncClient(
            base_url=_STRIPE_API,
            auth=(secret, ""),
            timeout=timeout,
            transport=self.transport,
        ) as client:
            try:
                response = await client.post("/payment_intents", data=form_data, headers=headers)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise ProviderIndeterminate() from exc
            except httpx.RequestError as exc:
                raise ProviderIndeterminate() from exc

            payload = self._json_object(response)
            if response.status_code >= 500:
                raise ProviderIndeterminate()
            if response.status_code >= 400:
                error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
                payment_intent = error.get("payment_intent")
                if isinstance(payment_intent, dict) and payment_intent.get("id"):
                    return await self._verify_payment_intent(client, payment_intent, amount_minor)
                raise ProviderFailure()

            if not isinstance(payload.get("id"), str) or not payload["id"].startswith("pi_"):
                raise ProviderFailure()
            return await self._verify_payment_intent(client, payload, amount_minor)

    async def _verify_payment_intent(
        self, client: httpx.AsyncClient, created: dict[str, Any], expected_amount: int
    ) -> SandboxResult:
        payment_intent_id = created.get("id")
        if not isinstance(payment_intent_id, str) or not payment_intent_id.startswith("pi_"):
            raise ProviderFailure()
        try:
            response = await client.get(f"/payment_intents/{quote(payment_intent_id, safe='')}")
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RequestError) as exc:
            raise ProviderIndeterminate() from exc
        if response.status_code >= 500:
            raise ProviderIndeterminate()
        if response.status_code >= 400:
            raise ProviderFailure()

        verified = self._json_object(response)
        if verified.get("id") != payment_intent_id:
            raise ProviderFailure()
        amount = verified.get("amount")
        currency = verified.get("currency")
        status = verified.get("status")
        if not isinstance(amount, int) or amount != expected_amount:
            raise ProviderFailure()
        if not isinstance(currency, str) or currency.lower() != "usd":
            raise ProviderFailure()
        if not isinstance(status, str):
            raise ProviderFailure()

        last_error = verified.get("last_payment_error")
        if status == "succeeded":
            outcome = "sandbox_approved"
        elif status == "requires_payment_method" and isinstance(last_error, dict):
            outcome = "sandbox_declined"
        elif status in {"canceled", "requires_action", "processing", "requires_capture"}:
            outcome = "sandbox_not_completed"
        else:
            outcome = "sandbox_pending"
        return SandboxResult(
            provider="stripe_test_mode",
            reference=payment_intent_id,
            status=status,
            outcome=outcome,
            amount_minor=amount,
            currency=currency.upper(),
            test_mode=True,
            method_label="Stripe documented test PaymentMethod: pm_card_visa",
        )

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except (ValueError, TypeError):
            return {}
        return body if isinstance(body, dict) else {}
