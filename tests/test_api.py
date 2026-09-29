from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from projectcontrol.server import create_app  # noqa: E402
from projectcontrol.store import Store  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path):
    app = create_app(Store(tmp_path / "db.json"))
    with TestClient(app) as test_client:
        yield test_client


def _project_body(root: Path, **overrides) -> dict:
    body = {
        "name": "demo",
        "root": str(root),
        "kind": "python",
        "interpreter": sys.executable,
        "log_lines": 200,
        "targets": [
            {
                "name": "printer",
                "kind": "task",
                "args": ["-c", "print('hello from projectcontrol')"],
                "autorestart": False,
                "stop_timeout": 1,
            }
        ],
    }
    body.update(overrides)
    return body


def test_state_and_crud(client, tmp_path: Path) -> None:
    state = client.get("/api/state").json()
    assert state["projects"] == []
    assert any(t["builtin"] for t in state["templates"])

    created = client.post("/api/projects", json=_project_body(tmp_path))
    assert created.status_code == 200, created.text
    project = created.json()
    assert project["name"] == "demo"
    target_id = project["targets"][0]["id"]
    assert project["targets"][0]["argv_preview"][:2] == [sys.executable, "-c"]

    listed = client.get("/api/projects").json()
    assert len(listed) == 1

    patched = client.patch(
        "/api/projects/" + project["id"], json=_project_body(tmp_path, name="renamed")
    ).json()
    assert patched["name"] == "renamed"
    assert patched["id"] == project["id"]
    assert patched["targets"][0]["id"] == target_id

    assert client.delete("/api/projects/" + project["id"]).json()["ok"] is True
    assert client.get("/api/projects").json() == []


def test_start_logs_and_stop(client, tmp_path: Path) -> None:
    project = client.post("/api/projects", json=_project_body(tmp_path)).json()
    pid = project["id"]
    tid = project["targets"][0]["id"]

    started = client.post(f"/api/projects/{pid}/targets/{tid}/start").json()
    assert started["result"]["ok"] is True, started

    deadline = time.monotonic() + 10
    texts: list[str] = []
    while time.monotonic() < deadline:
        logs = client.get(f"/api/projects/{pid}/logs").json()
        texts = [e["text"] for e in logs["entries"]]
        if any("hello from projectcontrol" in t for t in texts):
            break
        time.sleep(0.1)
    assert any("hello from projectcontrol" in t for t in texts), texts
    assert logs["stats"]["maxlen"] == 200

    client.post(f"/api/projects/{pid}/targets/{tid}/stop")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = client.get("/api/projects").json()[0]["statuses"][tid]["state"]
        if status == "stopped":
            break
        time.sleep(0.1)
    assert status == "stopped"

    assert client.delete(f"/api/projects/{pid}/logs").json()["ok"] is True
    assert client.get(f"/api/projects/{pid}/logs").json()["entries"] == []


def test_log_limit_trims_old_lines(client, tmp_path: Path) -> None:
    body = _project_body(tmp_path, log_lines=100)
    body["targets"] = [{
        "name": "noisy",
        "kind": "task",
        "args": ["-c", "for i in range(5000): print(i)"],
        "autorestart": False,
    }]
    project = client.post("/api/projects", json=body).json()
    pid, tid = project["id"], project["targets"][0]["id"]
    client.post(f"/api/projects/{pid}/targets/{tid}/start")

    deadline = time.monotonic() + 20
    data = {}
    while time.monotonic() < deadline:
        data = client.get(f"/api/projects/{pid}/logs?limit=100000").json()
        if data["dropped"] > 0:
            break
        time.sleep(0.2)
    assert data["stats"]["kept"] <= 100
    assert data["stats"]["maxlen"] == 100
    assert data["dropped"] > 0
    client.post(f"/api/projects/{pid}/targets/{tid}/stop")


def test_template_flow(client, tmp_path: Path) -> None:
    tpl = client.post("/api/templates", json={
        "name": "My celery",
        "kind": "celery",
        "targets": [
            {"name": "worker", "kind": "worker", "command": ["celery", "-A", "src.settings.celery", "worker", "--pool=solo", "--loglevel=info"]},
            {"name": "beat", "kind": "beat", "command": ["celery", "-A", "src.settings.celery", "beat", "--loglevel=info"]},
        ],
    }).json()
    assert tpl["name"] == "My celery"

    names = [t["name"] for t in client.get("/api/templates").json()]
    assert names.count("My celery") == 1
    assert client.delete("/api/templates/" + tpl["id"]).json()["ok"] is True
    assert client.delete("/api/templates/" + tpl["id"]).json()["ok"] is False


def test_detect_and_fs_endpoints(client, tmp_path: Path) -> None:
    (tmp_path / "manage.py").write_text("", encoding="utf-8")
    info = client.get("/api/detect", params={"root": str(tmp_path)}).json()
    assert info["kind"] == "django"

    listing = client.get("/api/fs/list", params={"path": str(tmp_path)}).json()
    assert any(e["name"] == "manage.py" for e in listing["entries"])
    assert listing["parent"]

    roots = client.get("/api/fs/roots").json()
    assert roots["drives"]


def test_settings_persist(client, tmp_path: Path) -> None:
    updated = client.put("/api/settings", json={
        "host": "127.0.0.1",
        "port": 9999,
        "default_log_lines": 555,
        "open_browser": False,
        "max_history": 10,
    }).json()
    assert updated["default_log_lines"] == 555
    assert (tmp_path / "db.json").exists()


def test_websocket_hello(client) -> None:
    with client.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "hello"
        assert "projects" in msg and "settings" in msg


def test_start_project_starts_enabled_targets(client, tmp_path: Path) -> None:
    body = _project_body(tmp_path)
    body["targets"].append({
        "name": "second",
        "kind": "task",
        "args": ["-c", "import time; time.sleep(60)"],
        "enabled": False,
        "autorestart": False,
        "stop_timeout": 1,
    })
    project = client.post("/api/projects", json=body).json()
    res = client.post(f"/api/projects/{project['id']}/start").json()
    assert len(res["results"]) == 1
    client.post(f"/api/projects/{project['id']}/stop")
