from __future__ import annotations

import secrets
from datetime import UTC, datetime
from decimal import Decimal


class SyntheticDataGenerator:
    """Generate explicitly fictional test records without payment credentials."""

    def generate_profile(self) -> dict[str, str]:
        token = secrets.token_hex(3).upper()
        return {
            "synthetic_label": "SYNTHETIC TEST DATA — NOT A REAL PERSON",
            "profile_id": f"SYN-PROFILE-{token}",
            "name": f"Example Customer {token[-4:]}",
            "email": f"synthetic-{token.lower()}@example.invalid",
            "country": "ZZ",
        }

    def generate_transactions(self, count: int, max_count: int = 20) -> list[dict[str, str | int]]:
        if not isinstance(count, int) or isinstance(count, bool) or count < 1 or count > max_count:
            raise ValueError(f"Count must be between 1 and {max_count}.")
        now = datetime.now(UTC)
        rows: list[dict[str, str | int]] = []
        for index in range(count):
            token = secrets.token_hex(4).upper()
            cents = secrets.randbelow(499_900) + 100
            rows.append(
                {
                    "synthetic_label": "synthetic",
                    "transaction_id": f"SYN-TXN-{token}",
                    "customer_id": f"SYN-CUST-{token[:6]}",
                    "merchant_id": f"SYN-MERCHANT-{secrets.token_hex(2).upper()}",
                    "amount": str((Decimal(cents) / Decimal(100)).quantize(Decimal("0.01"))),
                    "currency": "USD",
                    "timestamp": now.isoformat(),
                    "country": "ZZ",
                    "location": "TEST-NORTH" if index % 2 == 0 else "TEST-SOUTH",
                    "attempt_count": 1,
                    "status": "synthetic_test",
                }
            )
        return rows
