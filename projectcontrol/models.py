from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .config import MAX_LOG_LINES, MIN_LOG_LINES

TargetKind = Literal["web", "worker", "beat", "task", "custom"]


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Target(BaseModel):
    """One runnable process inside a project (web server, celery worker, beat...)."""

    id: str = Field(default_factory=new_id)
    name: str = "app"
    kind: TargetKind = "web"
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

    @field_validator("restart_delay", "stop_timeout", mode="before")
    @classmethod
    def _non_negative_float(cls, v: Any) -> float:
        try:
            return max(0.0, float(v))
        except (TypeError, ValueError):
            return 0.0

    def argv(self, project: "Project") -> list[str]:
        """Полный argv для процесса, с раскрытием подстановок."""
        if self.command:
            return [expand(tok, project, self) for tok in self.command]
        interp = self.interpreter or project.interpreter
        if self.script:
            script = self.script if is_abs(self.script) else join_path(project.root, self.script)
            argv = [interp or "python", script]
        elif self.module:
            argv = [interp or "python", "-m", self.module]
        else:
            argv = [interp or "python"]
        argv.extend(self.args)
        return [expand(tok, project, self) for tok in argv]

    def workdir(self, project: "Project") -> str:
        candidate = self.cwd or project.cwd or project.root
        if is_abs(candidate):
            return candidate
        return join_path(project.root, candidate)


class Project(BaseModel):
    id: str = Field(default_factory=new_id)
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
    targets: list[Target] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    @field_validator("name")
    @classmethod
    def _name_fallback(cls, v: str, info: Any) -> str:
        return v.strip()

    @field_validator("log_lines")
    @classmethod
    def _clamp_log_lines(cls, v: int) -> int:
        try:
            v = int(v)
        except (TypeError, ValueError):
            return 2000
        return max(MIN_LOG_LINES, min(MAX_LOG_LINES, v))

    @field_validator("stop_timeout", mode="before")
    @classmethod
    def _non_negative(cls, v: Any) -> float:
        try:
            return max(0.0, float(v))
        except (TypeError, ValueError):
            return 5.0

    def target(self, target_id: str) -> Target | None:
        for target in self.targets:
            if target.id == target_id:
                return target
        return None

    def to_disk(self) -> dict:
        data = self.model_dump()
        if not data["name"]:
            data["name"] = base_name(self.root)
        return data


class Template(BaseModel):
    """Reusable run configuration (e.g. django + celery worker + beat)."""

    id: str = Field(default_factory=new_id)
    name: str
    kind: str = "custom"
    targets: list[Target] = Field(default_factory=list)
    interpreter: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    env_file: str = ".env"
    load_env_file: bool = True
    start_url: str = ""
    admin_url: str = ""
    open_with: str = ""
    notes: str = ""
    builtin: bool = False
    created_at: float = Field(default_factory=time.time)


class Settings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8770
    default_log_lines: int = 2000
    open_browser: bool = True
    max_history: int = 50
    autostart_saved: bool = True
    default_venv: str = ""


class Database(BaseModel):
    version: int = 1
    projects: list[Project] = Field(default_factory=list)
    templates: list[Template] = Field(default_factory=list)
    settings: Settings = Field(default_factory=Settings)
    history: list[str] = Field(default_factory=list)


def is_abs(path: str) -> bool:
    from pathlib import Path

    return Path(path).is_absolute()


def env_paths(value: str) -> tuple[str, str]:
    """Разбирает настройку окружения.

    Принимает папку venv (`.venv`), путь к python.exe внутри venv или просто
    путь к интерпретатору. Возвращает (папка venv, путь к python).
    Пустая строка -> окружение не активируется.
    """
    import os
    from pathlib import Path

    if not value:
        return "", ""
    path = Path(value).expanduser()
    bin_dir = "Scripts" if os.name == "nt" else "bin"
    exe_name = "python.exe" if os.name == "nt" else "python"

    if path.is_dir():
        candidate = path / bin_dir / exe_name
        if candidate.exists():
            return str(path), str(candidate)
        return "", ""

    parent = path.parent
    if parent.name.lower() in {"scripts", "bin"}:
        return str(parent.parent), str(path)
    return "", str(path)


def join_path(root: str, rel: str) -> str:
    from pathlib import Path

    return str((Path(root) / rel).resolve()) if rel else root


def base_name(root: str) -> str:
    from pathlib import Path

    return Path(root).name or root


def python_exe() -> str:
    import sys

    return sys.executable or "python"


def effective_interpreter(project: "Project", target: "Target | None" = None, default_venv: str = "") -> str:
    """Приоритет: интерпретатор процесса -> интерпретатор проекта -> общий .venv из настроек."""
    return (target.interpreter if target else "") or project.interpreter or default_venv


def expand(token: str, project: Project, target: Target | None = None) -> str:
    """Expand IDE-style placeholders: {root} {name} {port} {host} {python} {app}."""
    if "{" not in token:
        return token
    venv, python = env_paths(effective_interpreter(project, target))
    values = {
        "root": project.root,
        "name": project.name or base_name(project.root),
        "host": project.host,
        "port": str(project.port or 8000),
        "python": python or "python",
        "venv": venv,
        "interpreter": python or "python",
    }
    for key, value in values.items():
        token = token.replace("{" + key + "}", str(value))
    return token
