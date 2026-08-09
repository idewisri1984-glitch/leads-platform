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
        paragraphs = [f"Hello {context.recipient_name},"]
        if context.recipient_role is not None:
            paragraphs.append(
                f"Your role as {context.recipient_role} at {context.company_name} "
                "made this outreach relevant."
            )
        else:
            paragraphs.append(f"Your work at {context.company_name} made this outreach relevant.")

        location = ", ".join(
            value for value in (context.company_city, context.company_country) if value is not None
        )
        if context.company_industry is not None and location:
            paragraphs.append(
                f"{context.company_name} operates in {context.company_industry} in {location}."
            )
        elif context.company_industry is not None:
            paragraphs.append(f"{context.company_name} operates in {context.company_industry}.")
        elif location:
            paragraphs.append(f"{context.company_name} is based in {location}.")

        outreach = (
            f"I am {request.sender_name} from {request.sender_company}. "
            f"I am reaching out about {request.purpose}. "
            f"The immediate outreach goal is {context.task_title.rstrip('.')}."
        )
        if context.task_description_data is not None:
            outreach += f" {context.task_description_data}"
        if request.value_proposition is not None:
            outreach += f" {request.value_proposition}"
        paragraphs.extend((outreach, "Would a brief conversation be useful?"))
        body = "\n\n".join(paragraphs)
        return EmailDraftGenerationResult(
            subject=subject,
            text_body=body,
            language=request.language,
            provider=self.provider,
            model=self.model,
            prompt_version=request.prompt_version,
        )
