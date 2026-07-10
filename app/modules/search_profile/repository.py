from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.search_profile.models import SearchProfile


class SearchProfileRepository:
    """
    Repository for SearchProfile entity.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, search_profile: SearchProfile) -> SearchProfile:
        self.session.add(search_profile)
        self.session.commit()
        self.session.refresh(search_profile)

        return search_profile

    def get(self, profile_id: int) -> SearchProfile | None:
        statement = select(SearchProfile).where(SearchProfile.id == profile_id)
        return self.session.scalar(statement)

    def get_all(self) -> list[SearchProfile]:
        statement = select(SearchProfile).order_by(SearchProfile.id)
        return list(self.session.scalars(statement))

    def get_by_project(self, project_id: int) -> list[SearchProfile]:
        statement = (
            select(SearchProfile)
            .where(SearchProfile.project_id == project_id)
            .order_by(SearchProfile.id)
        )

        return list(self.session.scalars(statement))

    def update(self, search_profile: SearchProfile) -> SearchProfile:
        self.session.add(search_profile)
        self.session.commit()
        self.session.refresh(search_profile)

        return search_profile

    def delete(self, search_profile: SearchProfile) -> None:
        self.session.delete(search_profile)
        self.session.commit()
