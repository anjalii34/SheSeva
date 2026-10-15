import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["LGE_ANALYST_KEY"] = "analyst-test-key"
os.environ["LGE_ADMIN_KEY"] = "admin-test-key"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import seed  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402

ANALYST = {"X-API-Key": "analyst-test-key"}
ADMIN = {"X-API-Key": "admin-test-key"}


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    seed.run(demo=True)
    with TestClient(app) as c:
        yield c
