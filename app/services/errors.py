class ServiceError(Exception):
    """Base error whose public message is safe to show in Telegram."""

    public_message = "The requested operation could not be completed."


class InvalidInput(ServiceError):
    public_message = "That input is invalid. Check the format and try again."


class FeatureDisabled(ServiceError):
    public_message = "This feature is currently disabled by an administrator."


class IntegrationNotConfigured(ServiceError):
    public_message = "The requested integration is not configured yet."


class SandboxDisabled(ServiceError):
    public_message = "Sandbox operations are disabled. No provider request was sent."


class ProviderFailure(ServiceError):
    public_message = (
        "The provider could not complete the sandbox request. No result was recorded as approved."
    )


class ProviderIndeterminate(ServiceError):
    public_message = "The provider response could not be confirmed. The request is held safely for an idempotent retry."


class Unauthorized(ServiceError):
    public_message = "This action requires an authorized administrator."


class RateLimited(ServiceError):
    public_message = "You're moving too fast. Wait briefly and try again."


class QuotaExceeded(ServiceError):
    public_message = (
        "Today's plan quota has been reached. Try again after the daily reset or review Plans."
    )


class InsufficientCredits(ServiceError):
    public_message = (
        "There are not enough credits for this operation. No payment details were requested."
    )


class UserSuspended(ServiceError):
    public_message = "This account is suspended. Contact an authorized administrator."


class NotFound(ServiceError):
    public_message = "The requested record was not found."


class FileValidationError(ServiceError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.public_message = message
