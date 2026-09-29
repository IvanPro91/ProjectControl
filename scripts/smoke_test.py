"""Живая проверка: поднимает реальный сервер и прогоняет сценарий celery worker + beat."""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

work = Path(tempfile.mkdtemp(prefix="pc-smoke-"))
os.environ["PROJECTCONTROL_HOME"] = str(work / "home")
(work / "proj" / "src" / "settings").mkdir(parents=True)
(work / "proj" / "manage.py").write_text("print('django stub')\n", encoding="utf-8")
(work / "proj" / "src" / "settings" / "__init__.py").write_text("", encoding="utf-8")
(work / "proj" / "src" / "settings" / "celery.py").write_text(
    "from celery import Celery\napp = Celery('smoke')\n", encoding="utf-8"
)

import uvicorn

from projectcontrol.server import create_app
from projectcontrol.store import Store

store = Store()
app = create_app(store)
config = uvicorn.Config(app, host="127.0.0.1", port=8799, log_level="warning")
server = uvicorn.Server(config)
threading.Thread(target=server.run, daemon=True).start()

base = "http://127.0.0.1:8799"
for _ in range(100):
    try:
        import urllib.request

        urllib.request.urlopen(base + "/api/health", timeout=1).read()
        break
    except Exception:
        time.sleep(0.1)


def call(path, data=None, method=None):
    import urllib.request

    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        base + path, data=body, method=method or ("POST" if data is not None else "GET"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.loads(res.read() or "null")


ok = True


def check(label, cond, extra=""):
    global ok
    ok = ok and bool(cond)
    print(("  OK  " if cond else " FAIL ") + label + ("  " + str(extra) if extra else ""))


info = call("/api/detect?root=" + str(work / "proj").replace("\\", "/"))
check("detect: Django + celery найден", info["kind"] == "django" and info["celery"], info["kind"])
check("detect: 3 процесса (web/worker/beat)", [t["name"] for t in info["targets"]] == ["web", "worker", "beat"],
      [t["name"] for t in info["targets"]])
check("detect: -A src.settings.celery", info["targets"][1]["args"][1] == "src.settings.celery",
      info["targets"][1]["args"])

html = __import__("urllib.request", fromlist=["x"]).urlopen(base + "/").read().decode()
check("интерфейс отдаётся", "ProjectControl" in html and "/static/core.js" in html)
for asset in ("core.js", "logs.js", "cards.js", "editor.js", "app.js", "styles.css"):
    code = __import__("urllib.request", fromlist=["x"]).urlopen(base + "/static/" + asset).status
    check("статика " + asset, code == 200)

# создаём проект с настоящими процессами celery (через интерпретатор проекта)
project = call("/api/projects", {
    "name": "smoke",
    "root": str(work / "proj"),
    "kind": "django",
    "interpreter": sys.executable,
    "start_url": "http://127.0.0.1:8000",
    "admin_url": "http://127.0.0.1:8000/admin/",
    "open_with": "notepad {root}",
    "log_lines": 50,
    "targets": [
        {"name": "web", "kind": "web", "script": "manage.py", "args": ["runserver"]},
        {"name": "worker", "kind": "worker", "module": "celery",
         "args": ["-A", "src.settings.celery", "worker", "--pool=solo", "--loglevel=info"], "autorestart": False},
        {"name": "beat", "kind": "beat", "module": "celery",
         "args": ["-A", "src.settings.celery", "beat", "--loglevel=info"], "autorestart": False},
    ],
})
pid = project["id"]
tids = {t["name"]: t["id"] for t in project["targets"]}
check("проект создан, argv собран", project["targets"][2]["argv_preview"][1:4] == ["-m", "celery", "-A"],
      project["targets"][2]["argv_preview"])

res = call(f"/api/projects/{pid}/targets/{tids['worker']}/start", {})
check("celery worker запущен", res["result"].get("ok"), res["result"])

deadline = time.time() + 15
logs = []
while time.time() < deadline:
    logs = call(f"/api/projects/{pid}/logs?limit=500")["entries"]
    joined = " ".join(e["text"] for e in logs)
    if "celery@" in joined or "transport:" in joined or "ready" in joined:
        break
    time.sleep(0.2)
joined = " ".join(e["text"] for e in logs)
check("лог worker приходит в интерфейс", "celery@" in joined or "transport:" in joined,
      "\n         " + "\n         ".join(e["text"][:90] for e in logs[-6:]))
check("лог помечен именем процесса", any(e["target_name"] == "worker" for e in logs))

states = call("/api/projects") and call("/api/projects")[0]["statuses"]
check("статус worker = running", states[tids["worker"]]["state"] == "running", states[tids["worker"]])

call(f"/api/projects/{pid}/targets/{tids['worker']}/stop", {})
deadline = time.time() + 10
while time.time() < deadline:
    states = call("/api/projects")[0]["statuses"]
    if states[tids["worker"]]["state"] == "stopped":
        break
    time.sleep(0.2)
check("worker остановлен", states[tids["worker"]]["state"] == "stopped")

# ограничение логов
noisy = call("/api/projects", {
    "name": "noisy", "root": str(work / "proj"), "interpreter": sys.executable, "log_lines": 100,
    "targets": [{"name": "noisy", "kind": "task", "args": ["-c", "for i in range(20000): print(i)"],
                 "autorestart": False}],
})
call(f"/api/projects/{noisy['id']}/targets/{noisy['targets'][0]['id']}/start", {})
deadline = time.time() + 25
data = {}
while time.time() < deadline:
    data = call(f"/api/projects/{noisy['id']}/logs?limit=100000")
    if data["dropped"] > 1000:
        break
    time.sleep(0.3)
check("лог ограничен 100 строками", data["stats"]["kept"] <= 100, data["stats"])
call(f"/api/projects/{noisy['id']}/stop", {})
call("/api/stop-all", {})

call("/api/open-url", {"url": "http://127.0.0.1:9999"})  # не открывает ничего плохого
print("\nИТОГ:", "ВСЁ ОК" if ok else "ЕСТЬ ОШИБКИ")
print("рабочие файлы:", work)
server.should_exit = True
time.sleep(0.6)
sys.exit(0 if ok else 1)
