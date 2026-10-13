from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Protocol

from app.integrations.base import BinMetadata
from app.services.errors import InvalidInput, NotFound

_PREFIX_PATTERN = re.compile(r"^(?:[0-9]{6}|[0-9]{8})$", re.ASCII)


class BinProvider(Protocol):
    async def lookup(self, prefix: str) -> BinMetadata | None: ...


@dataclass(frozen=True, slots=True)
class CachedMetadata:
    value: BinMetadata
    expires_at: float


class BinLookupService:
    def __init__(self, provider: BinProvider, cache_seconds: int = 86_400) -> None:
        self.provider = provider
        self.cache_seconds = cache_seconds
        self._cache: dict[str, CachedMetadata] = {}

    async def lookup(self, value: str) -> BinMetadata:
        prefix = value.strip()
        if not _PREFIX_PATTERN.fullmatch(prefix):
            raise InvalidInput(
                "Send only a 6- or 8-digit BIN/IIN prefix. Full card numbers and security codes are rejected."
            )
        cached = self._cache.get(prefix)
        if cached and cached.expires_at > time.monotonic():
            return cached.value
        self._cache.pop(prefix, None)
        result = await self.provider.lookup(prefix)
        if result is None:
            raise NotFound()
        if self.cache_seconds:
            self._cache[prefix] = CachedMetadata(result, time.monotonic() + self.cache_seconds)
        if len(self._cache) > 5_000:
            now = time.monotonic()
            self._cache = {key: item for key, item in self._cache.items() if item.expires_at > now}
        return result
