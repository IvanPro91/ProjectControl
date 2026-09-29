from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

from .config import DB_PATH
from .models import Database, Project, Settings, Target, Template


def builtin_templates() -> list[Template]:
    def celery(name: str, kind: str, app: str, tail: list[str]) -> Target:
        return Target(name=name, kind=kind, command=["celery", "-A", app, *tail], autostart=True)

    return [
        Template(
            name="Django",
            kind="django",
            targets=[
                Target(name="web", kind="web", script="manage.py", args=["runserver", "127.0.0.1:8000"], autostart=True)
            ],
            start_url="http://127.0.0.1:8000",
            admin_url="http://127.0.0.1:8000/admin/",
            notes="manage.py runserver",
            builtin=True,
        ),
        Template(
            name="Django + Celery (worker + beat)",
            kind="django",
            targets=[
                Target(name="web", kind="web", script="manage.py", args=["runserver", "127.0.0.1:8000"], autostart=True),
                celery("worker", "worker", "config.celery", ["worker", "--pool=solo", "--loglevel=info"]),
                celery("beat", "beat", "config.celery", ["beat", "--loglevel=info"]),
            ],
            start_url="http://127.0.0.1:8000",
            admin_url="http://127.0.0.1:8000/admin/",
            notes="Поправь путь к приложению celery (config.celery -> свой)",
            builtin=True,
        ),
        Template(
            name="Django + Celery (src.settings.celery)",
            kind="django",
            targets=[
                Target(name="web", kind="web", script="manage.py", args=["runserver", "127.0.0.1:8000"], autostart=True),
                celery("worker", "worker", "src.settings.celery", ["worker", "--pool=solo", "--loglevel=info"]),
                celery("beat", "beat", "src.settings.celery", ["beat", "--loglevel=info"]),
            ],
            start_url="http://127.0.0.1:8000",
            admin_url="http://127.0.0.1:8000/admin/",
            notes="Если celery нет в PATH — запустится через <интерпретатор> -m celery",
            builtin=True,
        ),
        Template(
            name="Celery (только worker + beat)",
            kind="celery",
            targets=[
                celery("worker", "worker", "config.celery", ["worker", "--pool=solo", "--loglevel=info"]),
                celery("beat", "beat", "config.celery", ["beat", "--loglevel=info"]),
            ],
            notes="Работает и через `celery`, и через <интерпретатор> -m celery",
            builtin=True,
        ),
        Template(
            name="Celery Flower",
            kind="celery",
            targets=[
                Target(
                    name="flower",
                    kind="web",
                    command=["celery", "-A", "config.celery", "flower", "--port=5555"],
                    autostart=True,
                )
            ],
            start_url="http://127.0.0.1:5555",
            builtin=True,
        ),
        Template(
            name="FastAPI (uvicorn --reload)",
            kind="fastapi",
            targets=[
                Target(
                    name="web",
                    kind="web",
                    command=["uvicorn", "main:app", "--reload", "--host", "127.0.0.1", "--port", "8000"],
                    autostart=True,
                )
            ],
            start_url="http://127.0.0.1:8000/docs",
            builtin=True,
        ),
        Template(
            name="FastAPI (python -m uvicorn)",
            kind="fastapi",
            targets=[
                Target(
                    name="web",
                    kind="web",
                    module="uvicorn",
                    args=["main:app", "--reload", "--host", "127.0.0.1", "--port", "8000"],
                    autostart=True,
                )
            ],
            start_url="http://127.0.0.1:8000/docs",
            notes="Работает всегда, если uvicorn установлен в venv проекта",
            builtin=True,
        ),
        Template(
            name="Flask",
            kind="flask",
            targets=[
                Target(name="web", kind="web", module="flask", args=["run", "--debug", "--port", "5000"], autostart=True)
            ],
            start_url="http://127.0.0.1:5000",
            builtin=True,
        ),
        Template(
            name="Python-скрипт",
            kind="python",
            targets=[Target(name="app", kind="web", script="main.py")],
            builtin=True,
        ),
        Template(
            name="Node (npm run dev)",
            kind="node",
            targets=[Target(name="web", kind="web", command=["npm", "run", "dev"])],
            start_url="http://127.0.0.1:3000",
            builtin=True,
        ),
        Template(
            name="Пустой (настрою вручную)",
            kind="custom",
            targets=[Target(name="app", kind="web")],
            builtin=True,
        ),
    ]


class Store:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or DB_PATH)
        self._lock = threading.RLock()
        self._data = Database(projects=[], templates=builtin_templates())
        self.load()

    # ---- persistence -------------------------------------------------
    def load(self) -> None:
        with self._lock:
            raw: dict = {}
            if self.path.exists():
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8")) or {}
                except (OSError, ValueError):
                    raw = {}
            try:
                data = Database.model_validate(raw)
            except Exception:
                data = Database(projects=[], templates=builtin_templates())
            names = {t.name for t in data.templates}
            for template in builtin_templates():
                if template.name not in names:
                    data.templates.append(template)
            self._data = data

    def save(self) -> None:
        with self._lock:
            payload = self._data.model_dump(mode="json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # ---- projects ----------------------------------------------------
    @property
    def projects(self) -> list[Project]:
        with self._lock:
            return list(self._data.projects)

    def get(self, project_id: str) -> Project | None:
        with self._lock:
            for project in self._data.projects:
                if project.id == project_id:
                    return project
        return None

    def add(self, project: Project) -> Project:
        with self._lock:
            project.updated_at = time.time()
            if not project.name:
                project.name = Path(project.root).name or project.root
            self._data.projects.append(project)
            self._push_history(project.root)
        self.save()
        return project

    def update(self, project: Project) -> Project:
        with self._lock:
            project.updated_at = time.time()
            for index, existing in enumerate(self._data.projects):
                if existing.id == project.id:
                    self._data.projects[index] = project
                    break
            else:
                self._data.projects.append(project)
            self._push_history(project.root)
        self.save()
        return project

    def remove(self, project_id: str) -> bool:
        with self._lock:
            before = len(self._data.projects)
            self._data.projects = [p for p in self._data.projects if p.id != project_id]
            removed = len(self._data.projects) != before
        if removed:
            self.save()
        return removed

    # ---- templates ---------------------------------------------------
    @property
    def templates(self) -> list[Template]:
        with self._lock:
            return list(self._data.templates)

    def add_template(self, template: Template) -> Template:
        with self._lock:
            self._data.templates.append(template)
        self.save()
        return template

    def remove_template(self, template_id: str) -> bool:
        with self._lock:
            template = next((t for t in self._data.templates if t.id == template_id), None)
            if template is None or template.builtin:
                return False
            self._data.templates = [t for t in self._data.templates if t.id != template_id]
        self.save()
        return True

    # ---- settings & history -----------------------------------------
    @property
    def settings(self) -> Settings:
        with self._lock:
            return self._data.settings.model_copy()

    def update_settings(self, **changes) -> Settings:
        with self._lock:
            merged = {**self._data.settings.model_dump(), **changes}
            self._data.settings = Settings.model_validate(merged)
        self.save()
        return self._data.settings

    @property
    def history(self) -> list[str]:
        with self._lock:
            return list(self._data.history)

    def _push_history(self, root: str) -> None:
        if not root:
            return
        history = [h for h in self._data.history if h != root]
        history.insert(0, root)
        self._data.history = history[: max(5, self._data.settings.max_history)]
