from app.core.database import Base


def test_base_exists() -> None:
    assert Base is not None
