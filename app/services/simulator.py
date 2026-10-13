from __future__ import annotations

import csv
import io
import secrets
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True, slots=True)
class SimulationOutcome:
    scenario: str
    outcome: str
    amount_minor: int
    risk_score: int
    explanation: str
    transaction_id: str


class TransactionSimulator:
    """Local-only simulator. It does not call a payment provider."""

    SCENARIOS = {
        "routine": "A routine synthetic purchase with no elevated test signals.",
        "unusual_amount": "A synthetic amount above the lab's high-amount threshold.",
        "repeated_attempts": "A synthetic customer with repeated payment attempts.",
        "location_change": "A synthetic customer whose test locations change quickly.",
    }

    def __init__(
        self, rules: dict[str, dict[str, float]], max_amount_minor: int = 1_000_000
    ) -> None:
        self.rules = rules
        self.max_amount_minor = max_amount_minor

    def simulate(self, scenario: str, amount: str | Decimal | int) -> SimulationOutcome:
        if scenario == "random":
            scenario = secrets.choice(tuple(self.SCENARIOS))
        if scenario not in self.SCENARIOS:
            raise ValueError("Choose a supported local simulation scenario.")
        try:
            amount_decimal = Decimal(str(amount))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValueError("Enter a valid positive amount, such as 12.50.") from exc
        if not amount_decimal.is_finite() or amount_decimal <= 0:
            raise ValueError("Amount must be greater than zero.")
        if amount_decimal != amount_decimal.quantize(Decimal("0.01")):
            raise ValueError("Enter no more than two decimal places.")
        amount_minor = int(amount_decimal * 100)
        if amount_minor <= 0 or amount_minor > self.max_amount_minor:
            raise ValueError(f"Amount must be at most {self.max_amount_minor / 100:.2f} USD.")

        weights = self.rules[scenario]
        outcome = self._weighted_choice(weights)
        score = {
            "routine": 5,
            "unusual_amount": 45,
            "repeated_attempts": 55,
            "location_change": 70,
        }[scenario]
        if outcome == "suspicious":
            score = max(score, 65)
        elif outcome == "approved":
            score = min(score, 20)
        elif outcome == "simulated_error":
            score = 0

        explanation = self.SCENARIOS[scenario]
        if outcome == "approved":
            explanation += (
                " The simulator selected a synthetic approval; no real authorization occurred."
            )
        elif outcome == "declined":
            explanation += " The simulator selected a synthetic decline; no provider was contacted."
        elif outcome == "suspicious":
            explanation += (
                " The simulator selected a synthetic review outcome; no provider was contacted."
            )
        else:
            explanation += (
                " The simulator generated a synthetic error state; no provider was contacted."
            )

        return SimulationOutcome(
            scenario=scenario,
            outcome=outcome,
            amount_minor=amount_minor,
            risk_score=score,
            explanation=explanation,
            transaction_id=f"SIM-{secrets.token_hex(6).upper()}",
        )

    def simulate_batch(self, count: int, max_count: int = 20) -> list[SimulationOutcome]:
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= max_count:
            raise ValueError(f"Batch count must be between 1 and {max_count}.")
        scenarios = tuple(self.SCENARIOS)
        amount_floor = min(50, self.max_amount_minor)
        rows: list[SimulationOutcome] = []
        for _ in range(count):
            scenario = secrets.choice(scenarios)
            amount_minor = (
                secrets.randbelow(self.max_amount_minor - amount_floor + 1) + amount_floor
            )
            amount = Decimal(amount_minor) / Decimal(100)
            rows.append(self.simulate(scenario, amount))
        return rows

    @staticmethod
    def export_batch_csv(outcomes: list[SimulationOutcome]) -> bytes:
        output = io.StringIO(newline="")
        fields = [
            "synthetic_label",
            "transaction_id",
            "customer_id",
            "merchant_id",
            "scenario",
            "amount",
            "currency",
            "outcome",
            "risk_score",
            "explanation",
        ]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for item in outcomes:
            writer.writerow(
                {
                    "synthetic_label": "synthetic",
                    "transaction_id": item.transaction_id,
                    "customer_id": f"SYN-CUST-{item.transaction_id[-6:]}",
                    "merchant_id": "SYN-MERCHANT-TEST",
                    "scenario": item.scenario,
                    "amount": f"{item.amount_minor / 100:.2f}",
                    "currency": "USD",
                    "outcome": item.outcome,
                    "risk_score": item.risk_score,
                    "explanation": item.explanation,
                }
            )
        return output.getvalue().encode("utf-8")

    @staticmethod
    def _weighted_choice(weights: dict[str, float]) -> str:
        value = secrets.randbelow(1_000_000) / 1_000_000
        total = 0.0
        for outcome, probability in weights.items():
            total += probability
            if value < total:
                return outcome
        return next(reversed(weights))
