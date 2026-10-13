from __future__ import annotations

import httpx

from app.integrations.base import BinMetadata
from app.services.errors import IntegrationNotConfigured, ProviderFailure

_BINLIST_ENDPOINT = "https://lookup.binlist.net"


class BinlistProvider:
    """Public issuer/network metadata adapter. Only 6- or 8-digit prefixes are sent.

    Docs: https://binlist.net/
    """

    def __init__(
        self,
        *,
        enabled: bool,
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.enabled = enabled
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def lookup(self, prefix: str) -> BinMetadata | None:
        if not self.enabled:
            raise IntegrationNotConfigured()
        if len(prefix) not in {6, 8} or not prefix.isascii() or not prefix.isdigit():
            raise ValueError(
                "Enter exactly 6 or 8 leading BIN/IIN digits; do not submit a full card number."
            )

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds),
                transport=self.transport,
                headers={"Accept": "application/json", "User-Agent": "RoyalXChecker/1.0"},
            ) as client:
                response = await client.get(f"{_BINLIST_ENDPOINT}/{prefix}")
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RequestError) as exc:
            raise ProviderFailure() from exc

        if response.status_code == 404:
            return None
        if response.status_code == 429:
            raise ProviderFailure()
        if response.status_code >= 400:
            raise ProviderFailure()
        try:
            data = response.json()
        except (ValueError, TypeError) as exc:
            raise ProviderFailure() from exc
        if not isinstance(data, dict):
            raise ProviderFailure()

        bank = data.get("bank") if isinstance(data.get("bank"), dict) else {}
        country = data.get("country") if isinstance(data.get("country"), dict) else {}
        issuer = bank.get("name") if isinstance(bank.get("name"), str) else None
        country_name = country.get("name") if isinstance(country.get("name"), str) else None
        network = data.get("scheme") if isinstance(data.get("scheme"), str) else None
        card_type = data.get("type") if isinstance(data.get("type"), str) else None
        category = data.get("brand") if isinstance(data.get("brand"), str) else None
        return BinMetadata(
            prefix=prefix,
            network=network,
            card_type=card_type,
            category=category,
            country=country_name,
            issuer=issuer,
            source="Binlist public metadata API",
        )
