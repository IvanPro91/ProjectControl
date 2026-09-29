"""Сценарий «просто корневая папка + строка команды + галочки celery»."""
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

work = Path(tempfile.mkdtemp(prefix="pc-simple-"))
os.environ["PROJECTCONTROL_HOME"] = str(work / "home")

proj = work / "ConstructorSite"
(proj / "src" / "settings").mkdir(parents=True)
(proj / "app.py").write_text(
    "import time\nfor i in range(3):\n    print('app tick', i, flush=True)\ntime.sleep(60)\n",
    encoding="utf-8",
)
(proj / "src" / "settings" / "__init__.py").write_text("", encoding="utf-8")
(proj / "src" / "settings" / "celery.py").write_text(
    "from celery import Celery\napp = Celery('shop')\n", encoding="utf-8"
)

import uvicorn  # noqa: E402

from projectcontrol.server import create_app  # noqa: E402
from projectcontrol.store import Store  # noqa: E402

server = uvicorn.Server(uvicorn.Config(create_app(Store()), host="127.0.0.1", port=8798, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()

base = "http://127.0.0.1:8798"
for _ in range(100):
    try:
        urllib.request.urlopen(base + "/api/health", timeout=1).read()
        break
    except Exception:
        time.sleep(0.1)


def call(path, data=None, method=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(base + path, data=body,
                                 method=method or ("POST" if data is not None else "GET"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as res:
        return json.loads(res.read() or "null")


ok = True


def check(label, cond, extra=""):
    global ok
    ok = ok and bool(cond)
    print(("  OK  " if cond else " FAIL ") + label + ("  " + str(extra) if extra else ""))


# 1. Анализ папки — как это делает форма
info = call("/api/detect?root=" + str(proj).replace("\\", "/"))
check("celery найден и путь извлечён", info["celery"] and
      info["targets"][1]["args"][info["targets"][1]["args"].index("-A") + 1] == "src.settings.celery",
      info["targets"][1]["args"])

# 2. Ровно то, что заполняет простая форма: одна строка + галочки celery
python = sys.executable
main_cmd = f"{python} app.py"
worker_cmd = f"{python} -m celery -A src.settings.celery worker --pool=solo --loglevel=info"
beat_cmd = f"{python} -m celery -A src.settings.celery beat --loglevel=info"

project = call("/api/projects", {
    "name": "ConstructorSite",
    "root": str(proj),
    "kind": info["kind"],
    "interpreter": python,
    "start_url": "http://127.0.0.1:8000",
    "admin_url": "http://127.0.0.1:8000/admin/",
    "targets": [
        {"name": "ConstructorSite", "kind": "web", "command": main_cmd.split()},
        {"name": "worker", "kind": "worker", "command": worker_cmd.split()},
        {"name": "beat", "kind": "beat", "command": beat_cmd.split()},
    ],
})
pid = project["id"]
tids = {t["name"]: t["id"] for t in project["targets"]}
check("создано 3 процесса", list(tids) == ["ConstructorSite", "worker", "beat"], list(tids))

# 3. Все процессы работают из корня проекта
previews = call(f"/api/projects/{pid}/targets/{tids['worker']}/preview", {}, "POST")
check("cwd worker = корень проекта", previews["cwd"] == str(proj), previews["cwd"])

# 4. Запуск
for name in ("ConstructorSite", "worker", "beat"):
    res = call(f"/api/projects/{pid}/targets/{tids[name]}/start", {})
    check(f"запущен {name}", res["result"].get("ok"), res["result"])

deadline = time.time() + 25
texts = []
while time.time() < deadline:
    texts = [e["text"] for e in call(f"/api/projects/{pid}/logs?limit=900")["entries"]]
    joined = " ".join(texts)
    if "app tick 2" in joined and texts.count("celery") and any("beat" in t for t in texts):
        break
    time.sleep(0.3)

check("вывод app.py в логе", any("app tick" in t for t in texts))
check("логи celery помечены процессом",
      {e["target_name"] for e in call(f"/api/projects/{pid}/logs?limit=900")["entries"]}
      == {"ConstructorSite", "worker", "beat"},
      {e["target_name"] for e in call(f"/api/projects/{pid}/logs?limit=900")["entries"]})

# 5. Перезапуск всех
call(f"/api/projects/{pid}/restart", {})
time.sleep(2)
states = call("/api/projects")[0]["statuses"]
running = [s["state"] for s in states.values()]
check("все снова запущены после перезапуска", all(s == "running" for s in running), states)

# 6. Стоп-всё
call(f"/api/projects/{pid}/stop", {})
deadline = time.time() + 15
while time.time() < deadline:
    states = call("/api/projects")[0]["statuses"]
    if all(s["state"] == "stopped" for s in states.values()):
        break
    time.sleep(0.2)
check("все остановлены", all(s["state"] == "stopped" for s in states.values()), states)

print("\nИТОГ:", "ВСЁ ОК" if ok else "ЕСТЬ ОШИБКИ")
server.should_exit = True
time.sleep(0.5)
sys.exit(0 if ok else 1)
