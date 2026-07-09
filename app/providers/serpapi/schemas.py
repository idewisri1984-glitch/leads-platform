from pydantic import BaseModel


class SerpApiCompanyResult(BaseModel):
    """
    Minimal organic result fields needed for company discovery.
    """

    position: int | None
    title: str
    link: str | None
    snippet: str | None
    source: str | None


class SerpApiSearchResponse(BaseModel):
    """
    Parsed SerpAPI organic search response.
    """

    query: str
    results: list[SerpApiCompanyResult]
