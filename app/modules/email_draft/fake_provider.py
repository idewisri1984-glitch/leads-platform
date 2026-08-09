from dataclasses import dataclass, field

from .provider_interfaces import EmailDraftGenerator
from .schemas import EmailDraftGenerationResult, EmailDraftProviderRequest


@dataclass(slots=True)
class FakeEmailDraftGenerator(EmailDraftGenerator):
    provider: str = "fake"
    model: str = "deterministic-email-v1"
    calls: list[EmailDraftProviderRequest] = field(default_factory=list)

    def generate(self, request: EmailDraftProviderRequest) -> EmailDraftGenerationResult:
        self.calls.append(request)
        context = request.context
        subject = f"A practical idea for {context.company_name}"
        body = (
            f"Hello {context.recipient_name},\n\n"
            f"I am {request.sender_name} from {request.sender_company}. "
            f"I am reaching out about {request.purpose}. "
            f"Your work at {context.company_name} made this relevant."
        )
        if request.value_proposition is not None:
            body += f" {request.value_proposition}"
        body += "\n\nWould a brief conversation be useful?"
        return EmailDraftGenerationResult(
            subject=subject,
            text_body=body,
            language=request.language,
            provider=self.provider,
            model=self.model,
            prompt_version=request.prompt_version,
        )
