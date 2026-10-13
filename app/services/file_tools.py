from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.services.errors import FileValidationError
from app.services.fraud_detection import FraudDetectionService, RiskFinding

_ALLOWED_COLUMNS = {
    "synthetic_label",
    "transaction_id",
    "customer_id",
    "merchant_id",
    "amount",
    "currency",
    "timestamp",
    "country",
    "location",
    "attempt_count",
    "status",
}
_SENSITIVE_HEADER = re.compile(
    r"(?:^|_)(?:pan|card_?number|cc_?number|cvv|cvc|security_?code|track_?data|expiry|exp_?date)(?:$|_)",
    re.I,
)
_SENSITIVE_VALUE = re.compile(
    r"\b(?:cvv|cvc|security[_ -]?code)\s*(?:(?:[:=#-])\s*|(?:is|of)\s+)?\d{3,4}\b",
    re.I,
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$", re.ASCII)
_LOCATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ,.()_-]{0,79}$", re.ASCII)
_COUNTRY = re.compile(r"^[A-Z]{2}$", re.ASCII)
_CURRENCY = re.compile(r"^[A-Z]{3}$", re.ASCII)


@dataclass(frozen=True, slots=True)
class FileAnalysis:
    rows: list[dict[str, Any]]
    findings: list[RiskFinding]
    duplicate_count: int

    @property
    def high_risk_count(self) -> int:
        return sum(finding.level == "high" for finding in self.findings)

    @property
    def medium_risk_count(self) -> int:
        return sum(finding.level == "medium" for finding in self.findings)


def contains_payment_credentials(data: bytes | str) -> bool:
    text = data.decode("utf-8", errors="ignore") if isinstance(data, bytes) else data
    if _SENSITIVE_VALUE.search(text):
        return True
    for candidate in re.findall(r"(?<!\d)(?:\d[ -]?){11,18}\d(?!\d)", text):
        digits = re.sub(r"\D", "", candidate)
        if 12 <= len(digits) <= 19:
            return True
    return False


def analyze_synthetic_csv(
    filename: str,
    data: bytes,
    *,
    max_bytes: int,
    max_rows: int,
    fraud_detector: FraudDetectionService,
) -> FileAnalysis:
    if Path(filename).suffix.lower() not in {".csv", ".txt"}:
        raise FileValidationError("Upload a CSV or plain-text CSV file only.")
    if not data or len(data) > max_bytes:
        raise FileValidationError(f"File size must be between 1 byte and {max_bytes:,} bytes.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FileValidationError("File must use UTF-8 text encoding.") from exc
    if contains_payment_credentials(text):
        raise FileValidationError(
            "Payment card numbers or security codes are not accepted. Remove them and retry."
        )

    try:
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if not reader.fieldnames:
            raise FileValidationError("The file needs a CSV header row.")
        headers = [header.strip().lower() for header in reader.fieldnames if header is not None]
        if len(headers) != len(reader.fieldnames) or len(set(headers)) != len(headers):
            raise FileValidationError("CSV headers must be present and unique.")
        reader.fieldnames = headers
        if any(_SENSITIVE_HEADER.search(header) for header in headers):
            raise FileValidationError("Sensitive payment-data columns are not accepted.")
        unknown = set(headers) - _ALLOWED_COLUMNS
        if unknown:
            raise FileValidationError(
                "Unsupported column(s): "
                + ", ".join(sorted(unknown))
                + ". Only synthetic test fields are accepted."
            )
        if "synthetic_label" not in headers or "amount" not in headers:
            raise FileValidationError("Required columns: synthetic_label and amount.")

        rows: list[dict[str, Any]] = []
        seen: set[tuple[tuple[str, str], ...]] = set()
        duplicate_count = 0
        for row_number, raw in enumerate(reader, start=2):
            if row_number - 1 > max_rows:
                raise FileValidationError(
                    f"File exceeds the configured limit of {max_rows:,} rows."
                )
            if None in raw or any(value is None for value in raw.values()):
                raise FileValidationError(
                    f"Row {row_number} has a different number of values than the header."
                )
            clean = {key: (value or "").strip() for key, value in raw.items() if key is not None}
            if any(len(value) > 256 for value in clean.values()):
                raise FileValidationError(f"Row {row_number} contains a field over 256 characters.")
            if clean.get("synthetic_label", "").lower() not in {"synthetic", "test"}:
                raise FileValidationError(
                    f"Row {row_number} is not labelled as synthetic test data."
                )
            normalized: dict[str, Any] = {"synthetic_label": "synthetic"}

            for key in ("transaction_id", "customer_id", "merchant_id"):
                value = clean.get(key, "")
                if value:
                    if not _IDENTIFIER.fullmatch(value):
                        raise FileValidationError(f"Row {row_number} has an invalid {key} value.")
                    normalized[key] = value
            try:
                amount = Decimal(clean.get("amount", ""))
            except (InvalidOperation, ValueError) as exc:
                raise FileValidationError(f"Row {row_number} has an invalid amount.") from exc
            if not amount.is_finite() or amount <= 0 or amount > Decimal("10000000"):
                raise FileValidationError(
                    f"Row {row_number} amount must be above 0 and no more than 10000000."
                )
            normalized["amount"] = f"{amount:.2f}"

            currency = clean.get("currency", "USD").upper()
            if not _CURRENCY.fullmatch(currency):
                raise FileValidationError(f"Row {row_number} has an invalid currency code.")
            normalized["currency"] = currency

            timestamp = clean.get("timestamp", "")
            if timestamp:
                try:
                    parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise FileValidationError(
                        f"Row {row_number} has an invalid ISO-8601 timestamp."
                    ) from exc
                normalized["timestamp"] = parsed_timestamp.isoformat()
            for key in ("country", "location", "status"):
                value = clean.get(key, "")
                if value:
                    pattern = (
                        _COUNTRY
                        if key == "country"
                        else _LOCATION
                        if key == "location"
                        else _IDENTIFIER
                    )
                    candidate = value.upper() if key in {"country", "status"} else value
                    if not pattern.fullmatch(candidate):
                        raise FileValidationError(f"Row {row_number} has an invalid {key} value.")
                    normalized[key] = candidate
            if clean.get("attempt_count", ""):
                try:
                    attempts = int(clean["attempt_count"])
                except ValueError as exc:
                    raise FileValidationError(
                        f"Row {row_number} has an invalid attempt_count."
                    ) from exc
                if not 0 <= attempts <= 10_000:
                    raise FileValidationError(
                        f"Row {row_number} attempt_count must be between 0 and 10000."
                    )
                normalized["attempt_count"] = attempts
            else:
                normalized["attempt_count"] = 1

            duplicate_key = (
                (("transaction_id", str(normalized["transaction_id"])),)
                if normalized.get("transaction_id")
                else tuple(sorted((str(key), str(value)) for key, value in normalized.items()))
            )
            if duplicate_key in seen:
                duplicate_count += 1
            else:
                seen.add(duplicate_key)
            rows.append(normalized)

        if not rows:
            raise FileValidationError("The file contains no synthetic transaction rows.")
    except csv.Error as exc:
        raise FileValidationError("The file contains malformed CSV data.") from exc

    findings = fraud_detector.analyze(rows)
    return FileAnalysis(rows=rows, findings=findings, duplicate_count=duplicate_count)


def export_analysis_csv(analysis: FileAnalysis) -> bytes:
    output = io.StringIO(newline="")
    fields = [
        "synthetic_label",
        "transaction_id",
        "customer_id",
        "merchant_id",
        "amount",
        "currency",
        "timestamp",
        "country",
        "location",
        "attempt_count",
        "status",
        "risk_score",
        "risk_level",
        "triggered_rules",
    ]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row, finding in zip(analysis.rows, analysis.findings, strict=True):
        export_row = dict(row)
        for key, value in tuple(export_row.items()):
            if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
                export_row[key] = "'" + value
        export_row.update(
            risk_score=finding.score,
            risk_level=finding.level,
            triggered_rules="|".join(finding.rules),
        )
        writer.writerow(export_row)
    return output.getvalue().encode("utf-8")
