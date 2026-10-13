from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Callable


class RateLimiter:
    """In-process sliding-window limiter; use a shared store for multi-replica deployments."""

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, key: str) -> tuple[bool, float]:
        now = self._clock()
        cutoff = now - self.window_seconds
        async with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.limit:
                return False, max(0.0, self.window_seconds - (now - events[0]))
            events.append(now)
            if len(self._events) > 10_000:
                expired_keys = [
                    name for name, queue in self._events.items() if not queue or queue[-1] <= cutoff
                ]
                for name in expired_keys:
                    self._events.pop(name, None)
            return True, 0.0
