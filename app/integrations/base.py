from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SandboxResult:
    provider: str
    reference: str
    status: str
    outcome: str
    amount_minor: int
    currency: str
    test_mode: bool
    method_label: str


@dataclass(frozen=True, slots=True)
class BinMetadata:
    prefix: str
    network: str | None
    card_type: str | None
    category: str | None
    country: str | None
    issuer: str | None
    source: str


class SandboxPaymentProvider(Protocol):
    @property
    def status(self) -> str: ...

    async def run_test_payment(
        self, *, amount_minor: int, currency: str, idempotency_key: str
    ) -> SandboxResult: ...


class BinMetadataProvider(Protocol):
    async def lookup(self, prefix: str) -> BinMetadata | None: ...
