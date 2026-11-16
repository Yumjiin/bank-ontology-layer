import os
from pathlib import Path

from fastapi.testclient import TestClient

local = Path(__file__).resolve().parents[2] / "ontology" / "ontology.yaml"
os.environ.setdefault("ONTOLOGY_PATH", str(local if local.exists() else "/app/ontology/ontology.yaml"))

from main import app  # noqa: E402


def test_health_and_ontology():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        body = client.get("/ontology").json()
        assert "Customer" in body["object_types"]
        assert "active_customers" in body["metrics"]


def test_role_header_is_required():
    with TestClient(app) as client:
        res = client.post("/objects/search", json={"object_type": "Customer"})
        assert res.status_code == 422
