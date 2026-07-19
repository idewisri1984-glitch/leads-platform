from typing import Annotated

from pydantic import BaseModel, Field, StrictInt, field_validator

MAX_TITLE_LENGTH = 500
MAX_LINK_LENGTH = 2048
MAX_SNIPPET_LENGTH = 2000
MAX_SOURCE_LENGTH = 255


class SerpApiCompanyResult(BaseModel):
    """
    Minimal organic result fields needed for company discovery.
    """

    position: int | None
    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    link: str | None = Field(default=None, max_length=MAX_LINK_LENGTH)
    snippet: str | None = Field(default=None, max_length=MAX_SNIPPET_LENGTH)
    source: str | None = Field(default=None, max_length=MAX_SOURCE_LENGTH)


class SerpApiSearchResponse(BaseModel):
    """
    Parsed SerpAPI organic search response.
    """

    query: str
    results: list[SerpApiCompanyResult]
    total_results: Annotated[StrictInt, Field(ge=0)] | None = None

    @field_validator("total_results", mode="before")
    @classmethod
    def reject_invalid_total_results(cls, value: object) -> object:
        if value is None or (isinstance(value, int) and not isinstance(value, bool) and value >= 0):
            return value
        raise ValueError("total_results must be a non-negative integer or null.")
