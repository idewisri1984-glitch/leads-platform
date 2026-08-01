class OpenAIDecisionError(Exception):
    """Base controlled exception for the OpenAI decision boundary."""


class OpenAIDecisionConfigurationError(OpenAIDecisionError):
    def __init__(self) -> None:
        super().__init__("OpenAI decision configuration is invalid.")


class OpenAIDecisionAuthenticationError(OpenAIDecisionError):
    def __init__(self) -> None:
        super().__init__("OpenAI authentication failed.")


class OpenAIDecisionRateLimitError(OpenAIDecisionError):
    def __init__(self) -> None:
        super().__init__("OpenAI rate limit exceeded.")


class OpenAIDecisionRequestError(OpenAIDecisionError):
    def __init__(self) -> None:
        super().__init__("OpenAI decision request failed.")


class OpenAIDecisionIncompleteError(OpenAIDecisionError):
    def __init__(self) -> None:
        super().__init__("OpenAI decision did not complete.")


class OpenAIDecisionRefusalError(OpenAIDecisionError):
    def __init__(self) -> None:
        super().__init__("OpenAI decision was refused.")


class OpenAIDecisionResponseError(OpenAIDecisionError):
    def __init__(self) -> None:
        super().__init__("OpenAI decision response was invalid.")
