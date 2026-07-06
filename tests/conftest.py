import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

_TEST_DB_DIR = tempfile.TemporaryDirectory()
_TEST_DB_PATH = Path(_TEST_DB_DIR.name) / "test_leads_platform.db"

os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH.as_posix()}"
os.environ["DEBUG"] = "false"


def pytest_sessionstart(session: pytest.Session) -> None:
    from app.core.database.base import Base
    from app.core.database.engine import engine

    Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def clean_database() -> Generator[None]:
    from app.core.database.base import Base
    from app.core.database.session import SessionLocal

    with SessionLocal() as session:
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())

        session.commit()

    yield


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    from app.core.database.base import Base
    from app.core.database.engine import engine

    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    _TEST_DB_DIR.cleanup()
