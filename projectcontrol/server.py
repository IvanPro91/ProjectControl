from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, detect, opener
from .bus import EventBus
from .config import DB_PATH, STATIC_DIR
from .logs import RingLog
from .models import Database, Project, Settings, Target, Template, effective_interpreter, env_paths, new_id
from .runner import ProjectRunner, resolve_executable
from .store import Store

HIDDEN_PREFIXES = (".",)


# ---------------------------------------------------------------- schemas
class TargetIn(BaseModel):
    id: str | None = None
    name: str = "app"
    kind: str = "web"
    command: list[str] = Field(default_factory=list)
    interpreter: str = ""
    script: str = ""
    module: str = ""
    args: list[str] = Field(default_factory=list)
    cwd: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    autostart: bool = False
    autorestart: bool = True
    restart_delay: float = 10.0
    stop_timeout: float = 5.0

    def to_model(self) -> Target:
        data = self.model_dump()
        data["id"] = self.id or new_id()
        return Target.model_validate(data)


class ProjectIn(BaseModel):
    name: str = ""
    root: str
    kind: str = "auto"
    interpreter: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    env_file: str = ".env"
    load_env_file: bool = True
    cwd: str = ""
    host: str = "127.0.0.1"
    port: int = 0
    start_url: str = ""
    admin_url: str = ""
    open_with: str = ""
    notes: str = ""
    autostart_all: bool = False
    autorestart: bool = True
    restart_delay: float = 10.0
    stop_timeout: float = 5.0
    log_lines: int = 2000
    targets: list[TargetIn] = Field(default_factory=list)

    def to_model(self, existing: Project | None = None) -> Project:
        data = self.model_dump()
        data["targets"] = [t.to_model() for t in self.targets]
        if existing:
            data["id"] = existing.id
            data["created_at"] = existing.created_at
            if not data["name"]:
                data["name"] = existing.name
            data["targets"] = _merge_target_ids(data["targets"], existing)
        if not data["name"]:
            data["name"] = Path(data["root"]).name or data["root"]
        return Project.model_validate(data)


class TemplateIn(BaseModel):
    name: str
    kind: str = "custom"
    targets: list[TargetIn] = Field(default_factory=list)
    interpreter: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    env_file: str = ".env"
    load_env_file: bool = True
    start_url: str = ""
    admin_url: str = ""
    open_with: str = ""
    notes: str = ""

    def to_model(self) -> Template:
        data = self.model_dump()
        data["targets"] = [t.to_model() for t in self.targets]
        return Template.model_validate(data)


class OpenUrlIn(BaseModel):
    url: str


def _check_env(value: str) -> dict:
    """Проверяет путь к .venv: находит интерпретатор и его версию."""
    import subprocess
    import sys as _sys

    venv, python = env_paths(value)
    if not python:
        return {"ok": False, "venv": "", "python": "", "error": "Не похоже на venv: не найден python"}
    if not Path(python).exists():
        return {"ok": False, "venv": venv, "python": python, "error": f"Файл не найден: {python}"}
    version = ""
    try:
        proc = subprocess.run(  # noqa: S603
            [python, "-c", "import sys;print(sys.version.split()[0])"],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        version = (proc.stdout or "").strip().splitlines()[0] if proc.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError, IndexError):
        version = ""
    packages = []
    try:
        proc = subprocess.run(  # noqa: S603
            [python, "-c", "import importlib.metadata as m;print(' '.join(sorted({d.metadata['Name'] for d in m.distributions()})))"],
            capture_output=True, text=True, timeout=25,
        )
        packages = [p for p in (proc.stdout or "").split() if p]
    except (OSError, subprocess.SubprocessError):
        packages = []
    interesting = [p for p in packages if p.lower() in {
        "django", "fastapi", "flask", "celery", "uvicorn", "gunicorn", "httpx", "pydantic",
        "redis", "psycopg2", "psycopg", "sqlalchemy", "flower", "pytest",
    }]
    return {
        "ok": True,
        "venv": venv,
        "python": python,
        "version": version,
        "packages": len(packages),
        "interesting": sorted(interesting),
        "note": "" if venv else "Это просто интерпретатор, окружение не активируется",
    }


def _merge_target_ids(targets: list[Target], existing: Project) -> list[Target]:
    """Keep original target ids when the client omits them (match by name)."""
    by_id = {t.id for t in existing.targets}
    by_name = {t.name: t.id for t in existing.targets}
    used: set[str] = set()
    for target in targets:
        if target.id in by_id:
            used.add(target.id)
            continue
        candidate = by_name.get(target.name)
        if candidate and candidate not in used:
            target.id = candidate
            used.add(candidate)
    return targets


def _autostart_one(runner: ProjectRunner, store: Store, project: Project, target: Target) -> None:
    """Запуск сохранённых процессов при старте панели (с проверкой настроек)."""
    fresh = store.get(project.id)
    if fresh is None:
        return
    current = fresh.target(target.id)
    if current is None or not current.enabled:
        return
    result = runner.start(fresh, current)
    if result.get("ok") is False:
        runner._log_line(fresh, current, current.name, f"Автозапуск не удался: {result.get('error')}", "error")


# ---------------------------------------------------------------- app
def create_app(store: Store | None = None) -> FastAPI:
    store = store or Store()
    bus = EventBus()
    runner = ProjectRunner(bus, log_lines=store.settings.default_log_lines,
                           default_venv=lambda: store.settings.default_venv)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        bus.bind_loop(asyncio.get_running_loop())
        if store.settings.autostart_saved:
            delay = 0.6
            for project in store.projects:
                if not (project.autostart_all or any(t.autostart for t in project.targets)):
                    continue
                for target in project.targets:
                    if not target.enabled:
                        continue
                    threading.Timer(delay, _autostart_one, args=(runner, store, project, target)).start()
                    delay += 0.4
        yield
        runner.shutdown()

    app = FastAPI(title="Project Control", version=__version__, lifespan=lifespan)
    app.state.store = store
    app.state.bus = bus
    app.state.runner = runner

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # ---- helpers ---------------------------------------------------
    def project_or_404(project_id: str) -> Project:
        project = store.get(project_id)
        if project is None:
            raise HTTPException(404, "Проект не найден")
        return project

    def target_or_404(project: Project, target_id: str) -> Target:
        target = project.target(target_id)
        if target is None:
            raise HTTPException(404, "Процесс не найден")
        return target

    def payload(project: Project) -> dict:
        data = project.to_disk()
        data["statuses"] = runner.snapshot(project)
        log = runner.log_for(project.id)
        data["log_stats"] = log.stats()
        for target in data.get("targets", []):
            model = project.target(target["id"])
            try:
                target["argv_preview"] = [str(a) for a in model.argv(project)] if model else []
            except Exception:
                target["argv_preview"] = []
        if project.start_url:
            host, port = _split_url(project.start_url)
            data["url_alive"] = opener.check_port(host, port)
        else:
            data["url_alive"] = False
        return data

    def broadcast(event: dict) -> None:
        bus.publish(event)

    # ---- static ----------------------------------------------------
    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "version": __version__}

    # ---- meta ------------------------------------------------------
    @app.get("/api/state")
    async def get_state() -> dict:
        return {
            "projects": [payload(p) for p in store.projects],
            "templates": [t.model_dump(mode="json") for t in store.templates],
            "settings": store.settings.model_dump(mode="json"),
            "history": store.history,
            "version": __version__,
            "platform": sys.platform,
        }

    @app.put("/api/settings")
    async def put_settings(payload_in: Settings) -> dict:
        result = store.update_settings(**payload_in.model_dump())
        runner.set_default_venv(result.default_venv)
        return result.model_dump(mode="json")

    @app.post("/api/env/check")
    async def env_check(payload_in: OpenUrlIn) -> dict:
        return _check_env(payload_in.url)

    @app.get("/api/settings")
    async def get_settings() -> dict:
        return store.settings.model_dump(mode="json")

    @app.get("/api/templates")
    async def get_templates() -> list[dict]:
        return [t.model_dump(mode="json") for t in store.templates]

    @app.post("/api/templates")
    async def post_template(payload_in: TemplateIn) -> dict:
        return store.add_template(payload_in.to_model()).model_dump(mode="json")

    @app.delete("/api/templates/{template_id}")
    async def delete_template(template_id: str) -> dict:
        return {"ok": store.remove_template(template_id)}

    @app.get("/api/detect")
    async def api_detect(root: str = Query("")) -> dict:
        return detect.detect(root)

    # ---- filesystem browsing ---------------------------------------
    @app.get("/api/fs/list")
    async def fs_list(path: str = Query("")) -> dict:
        return _list_dir(path)

    @app.get("/api/fs/roots")
    async def fs_roots() -> dict:
        return _roots()

    @app.get("/api/fs/interpreter")
    async def fs_interpreter(path: str = Query("")) -> dict:
        return {"ok": True, "interpreter": detect.detect(path).get("interpreter", "")}

    # ---- projects --------------------------------------------------
    @app.get("/api/projects")
    async def get_projects() -> list[dict]:
        return [payload(p) for p in store.projects]

    @app.post("/api/projects")
    async def post_project(payload_in: ProjectIn) -> dict:
        project = payload_in.to_model()
        if not Path(project.root).is_dir():
            raise HTTPException(400, f"Папка не найдена: {project.root}")
        if store.get(project.id):
            store.update(project)
        else:
            store.add(project)
        broadcast({"type": "projects", "reason": "created"})
        return payload(project)

    @app.patch("/api/projects/{project_id}")
    async def patch_project(project_id: str, payload_in: ProjectIn) -> dict:
        project = payload_in.to_model(existing=project_or_404(project_id))
        if not Path(project.root).is_dir():
            raise HTTPException(400, f"Папка не найдена: {project.root}")
        store.update(project)
        runner.set_maxlen(project.id, project.log_lines)
        broadcast({"type": "projects", "reason": "updated", "project_id": project_id})
        return payload(project)

    @app.put("/api/projects/{project_id}")
    async def put_project(project_id: str, payload_in: ProjectIn) -> dict:
        return await patch_project(project_id, payload_in)

    @app.delete("/api/projects/{project_id}")
    async def delete_project(project_id: str) -> dict:
        project = project_or_404(project_id)
        runner.stop_project(project)
        ok = store.remove(project_id)
        broadcast({"type": "projects", "reason": "deleted", "project_id": project_id})
        return {"ok": ok}

    # ---- targets ---------------------------------------------------
    @app.post("/api/projects/{project_id}/targets")
    async def post_target(project_id: str, payload_in: TargetIn) -> dict:
        project = project_or_404(project_id)
        target = payload_in.to_model()
        project.targets.append(target)
        store.update(project)
        broadcast({"type": "projects", "reason": "target_added", "project_id": project_id})
        return {"target": target.model_dump(mode="json"), "project": payload(project)}

    @app.patch("/api/projects/{project_id}/targets/{target_id}")
    async def patch_target(project_id: str, target_id: str, payload_in: TargetIn) -> dict:
        project = project_or_404(project_id)
        index = next((i for i, t in enumerate(project.targets) if t.id == target_id), -1)
        if index < 0:
            raise HTTPException(404, "Процесс не найден")
        updated = payload_in.to_model()
        updated.id = target_id
        running = runner.status(project, project.targets[index])
        was_running = running["state"] == "running"
        project.targets[index] = updated
        store.update(project)
        if was_running:
            runner.stop(project, updated)
            runner.start(project, updated)
        broadcast({"type": "projects", "reason": "target_updated", "project_id": project_id})
        return {"target": updated.model_dump(mode="json"), "project": payload(project)}

    @app.delete("/api/projects/{project_id}/targets/{target_id}")
    async def delete_target(project_id: str, target_id: str) -> dict:
        project = project_or_404(project_id)
        target = project.target(target_id)
        if target is None:
            raise HTTPException(404, "Процесс не найден")
        runner.stop(project, target)
        project.targets = [t for t in project.targets if t.id != target_id]
        store.update(project)
        broadcast({"type": "projects", "reason": "target_deleted", "project_id": project_id})
        return payload(project)

    # ---- launch control --------------------------------------------
    @app.post("/api/projects/{project_id}/targets/{target_id}/start")
    async def start_target(project_id: str, target_id: str) -> dict:
        project = project_or_404(project_id)
        target = target_or_404(project, target_id)
        result = runner.start(project, target)
        return {"result": result, "status": runner.status(project, target)}

    @app.post("/api/projects/{project_id}/targets/{target_id}/stop")
    async def stop_target(project_id: str, target_id: str, force: bool = False) -> dict:
        project = project_or_404(project_id)
        target = target_or_404(project, target_id)
        return {"result": runner.stop(project, target, force=force), "status": runner.status(project, target)}

    @app.post("/api/projects/{project_id}/targets/{target_id}/restart")
    async def restart_target(project_id: str, target_id: str) -> dict:
        project = project_or_404(project_id)
        target = target_or_404(project, target_id)
        runner.stop(project, target)
        result = runner.start(project, target)
        return {"result": result, "status": runner.status(project, target)}

    @app.post("/api/projects/{project_id}/targets/{target_id}/preview")
    async def preview_target(project_id: str, target_id: str) -> dict:
        project = project_or_404(project_id)
        target = target_or_404(project, target_id)
        argv = [str(a) for a in target.argv(project)]
        argv, error, note = resolve_executable(argv, project, target, store.settings.default_venv)
        venv, python = env_paths(effective_interpreter(project, target, store.settings.default_venv))
        return {"argv": argv, "error": error, "note": note, "cwd": target.workdir(project),
                "venv": venv, "python": python}

    @app.post("/api/projects/{project_id}/start")
    async def start_project(project_id: str) -> dict:
        project = project_or_404(project_id)
        results = []
        for target in project.targets:
            if not target.enabled:
                continue
            results.append({"target": target.name, **runner.start(project, target)})
        return {"results": results, "statuses": runner.snapshot(project)}

    @app.post("/api/projects/{project_id}/stop")
    async def stop_project(project_id: str) -> dict:
        project = project_or_404(project_id)
        runner.stop_project(project)
        return {"statuses": runner.snapshot(project)}

    @app.post("/api/projects/{project_id}/restart")
    async def restart_project(project_id: str) -> dict:
        project = project_or_404(project_id)
        runner.stop_project(project)
        results = []
        for target in project.targets:
            if not target.enabled:
                continue
            results.append({"target": target.name, **runner.start(project, target)})
        return {"results": results, "statuses": runner.snapshot(project)}

    @app.post("/api/stop-all")
    async def stop_all() -> dict:
        for project in store.projects:
            runner.stop_project(project)
        return {"ok": True}

    # ---- logs ------------------------------------------------------
    @app.get("/api/projects/{project_id}/logs")
    async def get_logs(
        project_id: str,
        since: int = 0,
        limit: int = 1000,
        target_id: str = "",
    ) -> dict:
        project = project_or_404(project_id)
        runner.log_for(project.id, project.log_lines)
        entries, dropped = runner.tail(project.id, since=since, limit=limit, maxlen=project.log_lines)
        if target_id:
            entries = [e for e in entries if e.target_id == target_id]
        stats = runner.log_for(project.id).stats()
        return {
            "project_id": project_id,
            "entries": [e.as_dict() for e in entries],
            "dropped": dropped,
            "stats": stats,
        }

    @app.delete("/api/projects/{project_id}/logs")
    async def clear_logs(project_id: str) -> dict:
        runner.log_for(project_id).clear()
        broadcast({"type": "logs_cleared", "project_id": project_id})
        return {"ok": True}

    # ---- external actions ------------------------------------------
    @app.post("/api/projects/{project_id}/open-folder")
    async def open_folder(project_id: str) -> dict:
        project = project_or_404(project_id)
        return opener.open_folder(project.root)

    @app.post("/api/projects/{project_id}/open-ide")
    async def open_ide(project_id: str) -> dict:
        project = project_or_404(project_id)
        if not project.open_with:
            raise HTTPException(400, "Не задана команда открытия в IDE")
        return opener.open_ide(project.open_with, project.root)

    @app.post("/api/projects/{project_id}/open-url")
    async def open_project_url(project_id: str) -> dict:
        project = project_or_404(project_id)
        if not project.start_url:
            raise HTTPException(400, "Не задан адрес проекта (start_url)")
        return opener.open_url(project.start_url)

    @app.post("/api/projects/{project_id}/open-admin")
    async def open_admin(project_id: str) -> dict:
        project = project_or_404(project_id)
        url = project.admin_url
        if not url:
            url = (project.start_url or "").rstrip("/") + "/admin/"
        if not url:
            raise HTTPException(400, "Не задан адрес админки")
        return opener.open_url(url)

    @app.post("/api/open-url")
    async def open_url(payload_in: OpenUrlIn) -> dict:
        return opener.open_url(payload_in.url)

    # ---- websocket -------------------------------------------------
    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        sub_id, queue = bus.subscribe()
        try:
            await ws.send_json(
                {
                    "type": "hello",
                    "projects": [payload(p) for p in store.projects],
                    "templates": [t.model_dump(mode="json") for t in store.templates],
                    "settings": store.settings.model_dump(mode="json"),
                    "history": store.history,
                    "version": __version__,
                }
            )
            while True:
                event = await queue.get()
                await ws.send_json(event)
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            pass
        finally:
            bus.unsubscribe(sub_id)

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    return app


def _split_url(url: str) -> tuple[str, int]:
    from urllib.parse import urlparse

    parsed = urlparse(url if "://" in url else "http://" + url)
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        port = 0
    return parsed.hostname or "", port


def _roots() -> dict:
    if sys.platform == "win32":
        import string

        drives = []
        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            if Path(root).exists():
                drives.append({"name": root, "path": root})
        home = {"name": "Домашняя папка", "path": str(Path.home())}
        return {"drives": drives, "home": home}
    return {
        "drives": [
            {"name": "/", "path": "/"},
            {"name": "~", "path": str(Path.home())},
        ],
        "home": {"name": "Домашняя папка", "path": str(Path.home())},
    }


def _list_dir(path: str) -> dict:
    if not path:
        return {"path": "", "parent": "", "entries": [], "error": "Не указан путь"}
    current = Path(path).expanduser()
    if not current.exists():
        current = Path(str(current).rsplit("\\", 1)[0] or str(current).rsplit("/", 1)[0] or Path.home())
    try:
        entries = []
        for item in sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if item.name.startswith(HIDDEN_PREFIXES):
                continue
            entries.append(
                {
                    "name": item.name,
                    "path": str(item),
                    "is_dir": item.is_dir(),
                }
            )
        return {
            "path": str(current),
            "parent": str(current.parent) if current.parent != current else "",
            "entries": entries,
        }
    except PermissionError:
        return {"path": str(current), "parent": str(current.parent), "entries": [], "error": "Нет доступа"}
    except OSError as exc:
        return {"path": str(current), "parent": "", "entries": [], "error": str(exc)}


app = None


def get_app() -> FastAPI:
    global app
    if app is None:
        app = create_app()
    return app


def port_is_free(host: str, port: int) -> bool:
    import socket

    bind_host = "" if host in {"0.0.0.0", "::", ""} else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((bind_host, int(port)))
        except OSError:
            return False
    return True


def find_free_port(host: str, port: int, attempts: int = 30) -> int:
    for offset in range(attempts):
        candidate = int(port) + offset
        if candidate > 65535:
            break
        if port_is_free(host, candidate):
            return candidate
    return int(port)


def main() -> int:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(prog="projectcontrol", description="Панель управления локальными проектами")
    parser.add_argument("--host", default=None, help="хост интерфейса")
    parser.add_argument("--port", type=int, default=None, help="порт интерфейса")
    parser.add_argument("--no-browser", action="store_true", help="не открывать браузер")
    parser.add_argument("--no-fallback-port", action="store_true", help="не искать свободный порт автоматически")
    parser.add_argument("--reload", action="store_true", help="авто-перезагрузка (для разработки)")
    args = parser.parse_args()

    store = Store()
    settings = store.settings
    host = args.host or settings.host or "127.0.0.1"
    wanted = int(args.port or settings.port or 8770)
    explicit = args.port is not None

    port = wanted
    if not port_is_free(host, port):
        if explicit or args.no_fallback_port:
            print("")
            print(f"  ОШИБКА: порт {port} уже занят.")
            print("  Освободи его (или закрой проект, который его использует) либо запусти с другим портом:")
            print(f"      python run.py --port {wanted + 1}")
            print("")
            return 1
        port = find_free_port(host, wanted)
        if port != wanted:
            print("")
            print(f"  Порт {wanted} занят другим приложением.")
            print(f"  ProjectControl будет доступен на порту {port}.")
            print(f"  Чтобы закрепить порт, поменяй его в настройках или запусти: python run.py --port {wanted}")
            print("")

    url = f"http://{host}:{port}/"
    print("")
    print("  ===========================================================")
    print("   ProjectControl запущен")
    print("")
    print(f"   Интерфейс:  {url}")
    print(f"   API:        http://{host}:{port}/api/health")
    print(f"   Настройки:  {DB_PATH}")
    print("")
    print("   Остановить: Ctrl+C")
    print("  ===========================================================")
    print("")

    open_browser = settings.open_browser and not args.no_browser
    if open_browser:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    application = create_app(store)
    try:
        uvicorn.run(
            application,
            host=host,
            port=port,
            log_level="info",
            reload=args.reload and os.name != "nt",
        )
    except KeyboardInterrupt:
        print("\n  Остановлено.")
    except OSError as exc:
        print("")
        print(f"  ОШИБКА запуска сервера: {exc}")
        print(f"  Попробуй другой порт: python run.py --port {find_free_port(host, port + 1)}")
        print("")
        return 1
    return 0
