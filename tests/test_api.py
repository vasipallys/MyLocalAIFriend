import os

os.environ["PHOENIX_ENABLED"] = "false"

from fastapi.testclient import TestClient
from backend.api import app


def test_health():
    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_conversation_lifecycle():
    with TestClient(app) as client:
        created = client.post("/api/conversations").json()
        assert created["title"] == "New conversation"
        assert client.get(f"/api/conversations/{created['id']}/messages").json() == []
        assert client.delete(f"/api/conversations/{created['id']}").status_code == 204

