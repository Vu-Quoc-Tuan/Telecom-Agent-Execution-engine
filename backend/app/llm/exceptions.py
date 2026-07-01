from typing import Any


class LLMError(Exception):
    """Base exception exposed by the LLM layer."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        code: str | None = None,
        status_code: int | None = None,
        request_id: str | None = None,
        retryable: bool = False,
        cause: Exception | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.code = code
        self.status_code = status_code
        self.request_id = request_id
        self.retryable = retryable
        self.cause = cause
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.__class__.__name__,
            "message": self.message,
            "provider": self.provider,
            "code": self.code,
            "status_code": self.status_code,
            "request_id": self.request_id,
            "retryable": self.retryable,
            "details": self.details,
        }


class LLMConfigurationError(LLMError):
    pass


class LLMInvalidRequestError(LLMError):
    pass


class LLMProviderUnavailableError(LLMError):
    pass


class LLMAllProvidersFailedError(LLMError):
    def __init__(self, *, errors: list[LLMError]) -> None:
        providers = ", ".join(error.provider for error in errors)
        message = f"All configured LLM providers failed: {providers}"
        if len(errors) == 1:
            message = f"{message}. Last error: {errors[0].message}"
        super().__init__(
            message,
            provider="gateway",
            code="all_providers_failed",
            retryable=any(error.retryable for error in errors),
            details={"errors": [error.to_dict() for error in errors]},
        )
        self.errors = errors
