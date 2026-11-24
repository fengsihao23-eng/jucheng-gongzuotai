"""测试夹具。每个测试进程使用独立数据库（TEST_DB，默认 jucheng_test），会话开始时重建并写入种子数据。

“今天”固定为 FAKE_TODAY（默认 2026-10-20），保证验收数字可复现。
"""
import os
import subprocess
import tempfile

TEST_DB = os.environ.get("TEST_DB", "jucheng_test")
os.environ.setdefault("FAKE_TODAY", "2026-10-20")
os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = f"postgresql+psycopg://localhost/{TEST_DB}"
os.environ["DATA_SOURCE_MODE"] = "mock"
os.environ["DATA_SOURCE_OVERRIDES"] = ""
os.environ["STORAGE_DIR"] = os.path.join(tempfile.gettempdir(), f"{TEST_DB}_var", "files")
os.environ["LLM_API_KEY"] = ""
os.environ["ASSISTANT_HOST_ACCESS"] = "false"
os.environ["ASSISTANT_HOST_HOME"] = os.path.join(tempfile.gettempdir(), f"{TEST_DB}_host")

subprocess.run(["createdb", TEST_DB], capture_output=True)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def seeded():
    from scripts import seed
    seed.main()
    yield


@pytest.fixture(scope="session")
def client(seeded):
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    from app.core.db import SessionLocal
    s = SessionLocal()
    yield s
    s.close()


_tokens: dict[str, str] = {}


@pytest.fixture(scope="session")
def login(client):
    """login("chenjing") -> {"Authorization": "Bearer ..."}"""
    from app.core.config import settings

    def _login(userid: str) -> dict:
        if userid not in _tokens:
            r = client.post("/api/v1/auth/login", json={"username": userid, "password": settings.demo_password})
            assert r.status_code == 200, r.text
            _tokens[userid] = r.json()["data"]["token"]
        return {"Authorization": f"Bearer {_tokens[userid]}"}
    return _login


def deletion_body(**fields):
    from app.core.config import settings

    return {"password": settings.operations_delete_password, **fields}


def data(resp, code: int = 0):
    """断言统一响应包并返回 data。"""
    body = resp.json()
    assert body["code"] == code, body
    return body["data"]
