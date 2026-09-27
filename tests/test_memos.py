import pytest
from fastapi.testclient import TestClient

from helpersonagent import api


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "test.db"))
    with TestClient(api.app) as c:
        yield c


def test_create_and_get_memo(client):
    created = client.post("/memos", json={"title": "첫 메모", "body": "안녕"})
    assert created.status_code == 201

    got = client.get(f"/memos/{created.json()['id']}")
    assert got.status_code == 200
    assert got.json()["title"] == "첫 메모"


def test_get_missing_memo_returns_404(client):
    assert client.get("/memos/999").status_code == 404
