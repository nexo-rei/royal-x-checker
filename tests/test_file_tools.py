from __future__ import annotations

import pytest

from app.services.errors import FileValidationError
from app.services.file_tools import (
    analyze_synthetic_csv,
    contains_payment_credentials,
    export_analysis_csv,
)
from app.services.fraud_detection import FraudDetectionService

VALID_CSV = (
    b"synthetic_label,transaction_id,customer_id,amount,currency,timestamp,country,location,attempt_count\n"
    b"synthetic,SYN-1,SYN-C1,12.50,USD,2026-01-01T12:00:00+00:00,ZZ,TEST-NORTH,1\n"
    b"synthetic,SYN-1,SYN-C1,12.50,USD,2026-01-01T12:00:00+00:00,ZZ,TEST-NORTH,1\n"
)


def test_csv_validation_duplicate_detection_and_safe_export() -> None:
    result = analyze_synthetic_csv(
        "sample.csv",
        VALID_CSV,
        max_bytes=10_000,
        max_rows=10,
        fraud_detector=FraudDetectionService(),
    )
    assert len(result.rows) == 2
    assert result.duplicate_count == 1
    exported = export_analysis_csv(result).decode()
    assert "risk_score" in exported
    assert "synthetic" in exported


def test_csv_headers_are_normalized_and_duplicate_ids_are_detected() -> None:
    content = (
        b"Synthetic_Label,Transaction_ID,Amount,Currency\n"
        b"synthetic,SYN-DUP,10.00,USD\n"
        b"synthetic,SYN-DUP,11.00,USD\n"
    )
    result = analyze_synthetic_csv(
        "sample.csv",
        content,
        max_bytes=10_000,
        max_rows=10,
        fraud_detector=FraudDetectionService(),
    )
    assert result.duplicate_count == 1


def test_sensitive_columns_full_pan_and_cvv_are_rejected() -> None:
    fraud = FraudDetectionService()
    with pytest.raises(FileValidationError, match="Sensitive payment-data"):
        analyze_synthetic_csv(
            "bad.csv",
            b"synthetic_label,amount,cvv\nsynthetic,10.00,123\n",
            max_bytes=10_000,
            max_rows=10,
            fraud_detector=fraud,
        )
    with pytest.raises(FileValidationError, match="card numbers"):
        analyze_synthetic_csv(
            "bad.csv",
            b"synthetic_label,amount,location\nsynthetic,10.00,4242 4242 4242 4242\n",
            max_bytes=10_000,
            max_rows=10,
            fraud_detector=fraud,
        )
    assert contains_payment_credentials("cvv=123") is True
    assert contains_payment_credentials("CVV 123") is True
    assert contains_payment_credentials("security code is 123") is True
    assert contains_payment_credentials("123456789012") is True
    assert contains_payment_credentials("1234567890123456") is True


def test_csv_size_row_count_and_synthetic_label_limits() -> None:
    fraud = FraudDetectionService()
    with pytest.raises(FileValidationError, match="size"):
        analyze_synthetic_csv(
            "sample.csv", VALID_CSV, max_bytes=5, max_rows=10, fraud_detector=fraud
        )
    with pytest.raises(FileValidationError, match="row"):
        analyze_synthetic_csv(
            "sample.csv", VALID_CSV, max_bytes=10_000, max_rows=1, fraud_detector=fraud
        )
    with pytest.raises(FileValidationError, match="not labelled"):
        analyze_synthetic_csv(
            "sample.csv",
            VALID_CSV.replace(b"synthetic,SYN-1", b"live,SYN-1"),
            max_bytes=10_000,
            max_rows=10,
            fraud_detector=fraud,
        )


def test_unknown_columns_and_extensions_are_rejected() -> None:
    fraud = FraudDetectionService()
    with pytest.raises(FileValidationError, match="Unsupported column"):
        analyze_synthetic_csv(
            "sample.csv",
            b"synthetic_label,amount,email\nsynthetic,10.00,person@example.com\n",
            max_bytes=10_000,
            max_rows=10,
            fraud_detector=fraud,
        )
    with pytest.raises(FileValidationError, match="CSV"):
        analyze_synthetic_csv(
            "payload.exe", VALID_CSV, max_bytes=10_000, max_rows=10, fraud_detector=fraud
        )
