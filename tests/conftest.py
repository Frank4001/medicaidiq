import pytest

from medicaidiq.paths import DB_PATH, KNOWLEDGE_PATH


@pytest.fixture(scope="session", autouse=True)
def built_data():
    """The synthetic environment is deterministic; build it once if missing."""
    if not DB_PATH.exists() or not KNOWLEDGE_PATH.exists():
        from medicaidiq.cli import build_data
        build_data()


@pytest.fixture(scope="session")
def db(built_data):
    from medicaidiq.db import ReadOnlyDB
    d = ReadOnlyDB()
    yield d
    d.close()


@pytest.fixture()
def toolbox(db):
    from medicaidiq.tools import Toolbox
    return Toolbox(db)
