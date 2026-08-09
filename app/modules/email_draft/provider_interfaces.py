from typing import Protocol

from .schemas import EmailDraftGenerationResult, EmailDraftProviderRequest


class EmailDraftProviderError(Exception):
    pass


class EmailDraftProviderConfigurationError(EmailDraftProviderError):
    pass


class EmailDraftProviderAuthenticationError(EmailDraftProviderError):
    pass


class EmailDraftProviderRateLimitError(EmailDraftProviderError):
    pass


class EmailDraftProviderTimeoutError(EmailDraftProviderError):
    pass


class EmailDraftProviderRefusalError(EmailDraftProviderError):
    pass


class EmailDraftProviderResponseError(EmailDraftProviderError):
    pass


class EmailDraftProviderUnavailableError(EmailDraftProviderError):
    pass


class EmailDraftGenerator(Protocol):
    def generate(self, request: EmailDraftProviderRequest) -> EmailDraftGenerationResult: ...
