import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_EXPORTS = ["Base", "SessionLocal", "engine"]


@pytest.mark.parametrize(
    "action",
    (
        "base-only",
        "engine-first",
        "session-then-engine",
        "submodule-then-engine",
        "repeated-engine",
        "unknown",
        "import-error",
        "sqlalchemy-lifecycle",
    ),
)
def test_database_exports_are_lazy_and_import_order_safe(action: str, tmp_path: Path) -> None:
    script = r"""
import importlib
import json
import os
import sys

action = sys.argv[1]
expected = json.loads(sys.argv[2])

if action == "base-only":
    import app.core.database as package
    assert package.__all__ == expected
    assert len(package.__all__) == len(set(package.__all__))
    from app.core.database import Base
    from app.core.database.base import Base as original
    assert Base is original
    assert "app.core.config.settings" not in sys.modules
    assert "app.core.database.engine" not in sys.modules
    assert "app.core.database.session" not in sys.modules
elif action == "unknown":
    import app.core.database as package
    try:
        package.not_an_export
    except AttributeError:
        pass
    else:
        raise AssertionError("unknown export did not raise AttributeError")
elif action == "import-error":
    import app.core.database as package
    package._EXPORTS["broken"] = ("app.missing_database_module", "value")
    try:
        package.broken
    except ModuleNotFoundError as error:
        assert error.name == "app.missing_database_module"
    else:
        raise AssertionError("ImportError was hidden")
else:
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import sessionmaker

    if action == "engine-first":
        from app.core.database import engine
    elif action == "session-then-engine":
        from app.core.database import SessionLocal
        from app.core.database import engine
        assert isinstance(SessionLocal, sessionmaker)
    elif action == "submodule-then-engine":
        module = importlib.import_module("app.core.database.engine")
        import app.core.database as package
        engine = package.engine
        assert engine is module.engine
    elif action == "repeated-engine":
        import app.core.database as package
        first = package.engine
        importlib.import_module("app.core.database.engine")
        second = package.engine
        assert first is second
        engine = second
    else:
        from sqlalchemy import Column, Integer, MetaData, Table, inspect

        from app.core.database import Base, SessionLocal, engine
        assert isinstance(SessionLocal, sessionmaker)
        assert SessionLocal.kw["bind"] is engine
        metadata = MetaData()
        Table("lazy_export_probe", metadata, Column("id", Integer, primary_key=True))
        metadata.create_all(bind=engine)
        with SessionLocal() as session:
            assert session.get_bind() is engine
        assert "lazy_export_probe" in inspect(engine).get_table_names()
        Base.metadata.create_all(bind=engine)
    assert isinstance(engine, Engine)
    engine_module = importlib.import_module("app.core.database.engine")
    assert engine is engine_module.engine
"""
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if action == "sqlalchemy-lifecycle":
        database = tmp_path / "database-lifecycle.sqlite3"
        environment["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    completed = subprocess.run(
        [sys.executable, "-c", script, action, json.dumps(EXPECTED_EXPORTS)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
