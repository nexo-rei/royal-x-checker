from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_SIMULATION_RULES: dict[str, dict[str, float]] = {
    "routine": {"approved": 0.80, "declined": 0.12, "suspicious": 0.06, "simulated_error": 0.02},
    "unusual_amount": {
        "approved": 0.14,
        "declined": 0.40,
        "suspicious": 0.44,
        "simulated_error": 0.02,
    },
    "repeated_attempts": {
        "approved": 0.09,
        "declined": 0.55,
        "suspicious": 0.34,
        "simulated_error": 0.02,
    },
    "location_change": {
        "approved": 0.14,
        "declined": 0.25,
        "suspicious": 0.59,
        "simulated_error": 0.02,
    },
}

_DEFAULT_FEATURE_COSTS = {
    "stripe_sandbox": 2,
    "bin_lookup": 1,
    "file_analysis": 1,
    "simulation": 0,
    "synthetic_data": 0,
    "fraud_analysis": 0,
}


class Settings(BaseSettings):
    """Validated application configuration. Secrets are never rendered in logs."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    bot_token: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("BOT_TOKEN", "bot_token")
    )
    owner_ids_raw: str = Field(
        default="", validation_alias=AliasChoices("OWNER_IDS", "owner_ids_raw")
    )
    database_url: str = "sqlite+aiosqlite:///./data/royal_x_checker.db"
    environment: str = "production"
    log_level: str = "INFO"

    stripe_sandbox_enabled: bool = False
    stripe_secret_key: SecretStr | None = None
    stripe_timeout_seconds: float = Field(default=12.0, gt=0, le=60)

    bin_lookup_enabled: bool = True
    bin_lookup_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    bin_lookup_cache_seconds: int = Field(default=86_400, ge=0, le=604_800)

    max_file_size_bytes: int = Field(default=1_048_576, ge=1_024, le=10_485_760)
    max_file_rows: int = Field(default=1_000, ge=1, le=10_000)
    max_test_count: int = Field(default=20, ge=1, le=100)
    max_simulation_amount_minor: int = Field(default=1_000_000, ge=1, le=100_000_000)
    max_feedback_chars: int = Field(default=2_000, ge=20, le=10_000)
    free_initial_credits: int = Field(default=5, ge=0, le=100_000)

    rate_limit_count: int = Field(default=30, ge=1, le=1_000)
    rate_limit_window_seconds: float = Field(default=60.0, gt=0, le=3_600)

    high_amount_threshold: float = Field(default=1_000.0, gt=0)
    repeated_attempt_threshold: int = Field(default=3, ge=1, le=100)
    frequency_threshold: int = Field(default=5, ge=1, le=10_000)
    location_change_window_minutes: int = Field(default=60, ge=1, le=10_080)
    simulation_rules: dict[str, dict[str, float]] = Field(
        default_factory=lambda: {
            name: rule.copy() for name, rule in _DEFAULT_SIMULATION_RULES.items()
        },
        validation_alias=AliasChoices("SIMULATION_RULES", "simulation_rules"),
    )
    feature_costs: dict[str, int] = Field(
        default_factory=lambda: _DEFAULT_FEATURE_COSTS.copy(),
        validation_alias=AliasChoices("FEATURE_COSTS", "feature_costs"),
    )

    @field_validator("owner_ids_raw")
    @classmethod
    def validate_owner_ids(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        parts = [part.strip() for part in value.split(",") if part.strip()]
        if any(not part.isdigit() or int(part) <= 0 for part in parts):
            raise ValueError(
                "OWNER_IDS must be a comma-separated list of positive Telegram user IDs"
            )
        return ",".join(dict.fromkeys(parts))

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: object) -> object:
        if isinstance(value, str) and value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        if isinstance(value, str) and value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value.startswith(("sqlite+aiosqlite:///", "postgresql+asyncpg://")):
            raise ValueError("DATABASE_URL must use sqlite+aiosqlite or postgresql+asyncpg")
        return value

    @field_validator("simulation_rules")
    @classmethod
    def validate_simulation_rules(
        cls, rules: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, float]]:
        allowed_scenarios = set(_DEFAULT_SIMULATION_RULES)
        outcomes = {"approved", "declined", "suspicious", "simulated_error"}
        for scenario, weights in rules.items():
            if scenario not in allowed_scenarios:
                raise ValueError(f"Unknown simulation scenario: {scenario}")
            if set(weights) != outcomes:
                raise ValueError(
                    f"Simulation scenario {scenario} must define exactly {sorted(outcomes)}"
                )
            normalized = {key: float(weight) for key, weight in weights.items()}
            if any(weight < 0 or weight > 1 for weight in normalized.values()):
                raise ValueError(f"Simulation probabilities for {scenario} must be between 0 and 1")
            if abs(sum(normalized.values()) - 1.0) > 1e-6:
                raise ValueError(f"Simulation probabilities for {scenario} must sum to 1")
            rules[scenario] = normalized
        if set(rules) != allowed_scenarios:
            raise ValueError(f"SIMULATION_RULES must define {sorted(allowed_scenarios)}")
        return rules

    @field_validator("feature_costs")
    @classmethod
    def validate_feature_costs(cls, costs: dict[str, int]) -> dict[str, int]:
        allowed = set(_DEFAULT_FEATURE_COSTS)
        if set(costs) != allowed:
            raise ValueError(f"FEATURE_COSTS must define exactly {sorted(allowed)}")
        if any(not isinstance(cost, int) or cost < 0 or cost > 10_000 for cost in costs.values()):
            raise ValueError("Feature costs must be integers between 0 and 10000")
        return costs

    @model_validator(mode="after")
    def validate_stripe_key(self) -> Settings:
        if self.stripe_secret_key and not self.stripe_secret_key.get_secret_value().startswith(
            "sk_test_"
        ):
            raise ValueError(
                "STRIPE_SECRET_KEY must be a Stripe test-mode key beginning with sk_test_"
            )
        return self

    @property
    def owner_ids(self) -> frozenset[int]:
        return frozenset(int(item) for item in self.owner_ids_raw.split(",") if item)

    def validate_runtime(self) -> None:
        if self.bot_token is None or not self.bot_token.get_secret_value().strip():
            raise ValueError(
                "BOT_TOKEN is required. Create a bot with BotFather and set BOT_TOKEN in the environment."
            )
        if not self.owner_ids:
            raise ValueError("OWNER_IDS must contain at least one authorized Telegram user ID.")

    @property
    def stripe_configured(self) -> bool:
        return self.stripe_sandbox_enabled and self.stripe_secret_key is not None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
