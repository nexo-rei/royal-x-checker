from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True, slots=True)
class RiskFinding:
    score: int
    level: str
    rules: tuple[str, ...]
    explanation: str


class FraudDetectionService:
    """Transparent, configurable rules over synthetic data; this is not a fraud verdict."""

    def __init__(
        self,
        *,
        high_amount_threshold: float = 1_000.0,
        repeated_attempt_threshold: int = 3,
        frequency_threshold: int = 5,
        location_change_window_minutes: int = 60,
    ) -> None:
        self.high_amount_threshold = Decimal(str(high_amount_threshold))
        self.repeated_attempt_threshold = repeated_attempt_threshold
        self.frequency_threshold = frequency_threshold
        self.location_window = timedelta(minutes=location_change_window_minutes)

    def analyze(self, records: list[dict[str, Any]]) -> list[RiskFinding]:
        customer_frequency = Counter(str(row.get("customer_id", "")) for row in records)
        location_changes = self._location_change_indexes(records)
        results: list[RiskFinding] = []

        for index, record in enumerate(records):
            score = 0
            rules: list[str] = []
            amount = self._decimal(record.get("amount", 0))
            if amount is not None and amount > self.high_amount_threshold:
                score += 30
                rules.append("unusual_amount")

            attempts = self._integer(record.get("attempt_count", 1), default=1)
            if attempts >= self.repeated_attempt_threshold:
                score += 25
                rules.append("repeated_attempts")

            customer_id = str(record.get("customer_id", ""))
            if customer_id and customer_frequency[customer_id] >= self.frequency_threshold:
                score += 20
                rules.append("high_transaction_frequency")

            if index in location_changes:
                score += 35
                rules.append("rapid_location_change")

            score = min(score, 100)
            level = "high" if score >= 60 else "medium" if score >= 30 else "low"
            explanation = (
                "Triggered: " + ", ".join(rules)
                if rules
                else "No configured heuristic threshold was triggered."
            )
            results.append(RiskFinding(score, level, tuple(rules), explanation))
        return results

    def _location_change_indexes(self, records: list[dict[str, Any]]) -> set[int]:
        grouped: dict[str, list[tuple[int, datetime, str]]] = defaultdict(list)
        for index, record in enumerate(records):
            customer_id = str(record.get("customer_id", ""))
            location = str(record.get("location") or record.get("country") or "").strip().upper()
            timestamp = self._datetime(record.get("timestamp"))
            if customer_id and location and timestamp:
                grouped[customer_id].append((index, timestamp, location))

        flagged: set[int] = set()
        for entries in grouped.values():
            entries.sort(key=lambda value: value[1])
            for previous, current in zip(entries, entries[1:], strict=False):
                if (
                    previous[2] != current[2]
                    and current[1] - previous[1] <= self.location_window
                    and current[1] >= previous[1]
                ):
                    flagged.add(current[0])
        return flagged

    @staticmethod
    def _decimal(value: Any) -> Decimal | None:
        try:
            number = Decimal(str(value))
            return number if number.is_finite() else None
        except (InvalidOperation, TypeError, ValueError):
            return None

    @staticmethod
    def _integer(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            result = value
        else:
            try:
                result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                return None
        if result.tzinfo is None:
            result = result.replace(tzinfo=UTC)
        return result.astimezone(UTC)
