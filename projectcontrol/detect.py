from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

WIN = sys.platform == "win32"

_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")

VENV_DIRS = (".venv", "venv", "env", ".env_venv", "envs")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def _venv_python(root: Path) -> str | None:
    for name in VENV_DIRS:
        venv = root / name
        if WIN:
            candidate = venv / "Scripts" / "python.exe"
        else:
            candidate = venv / "bin" / "python"
        if candidate.exists():
            return str(candidate)
    return None


def _node_modules_bin(root: Path, names: tuple[str, ...]) -> str | None:
    for name in names:
        candidate = root / "node_modules" / ".bin" / name
        if WIN:
            for suffix in (".cmd", ".exe", ""):
                with_suffix = candidate.with_name(candidate.name + suffix)
                if with_suffix.exists():
                    return str(with_suffix)
        elif candidate.exists():
            return str(candidate)
    return None


def _read(path: Path, limit: int = 400_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def _settings_modules(root: Path) -> list[str]:
    mods: list[str] = []
    for pkg in (root, root / "src", root / "app", root / "backend"):
        if not pkg.is_dir():
            continue
        settings_dir = pkg / "settings"
        if settings_dir.is_dir():
            for candidate in sorted(settings_dir.glob("*.py")):
                stem = candidate.stem
                if stem in {"__init__", "base", "test", "tests"}:
                    continue
                prefix = "" if pkg == root else f"{pkg.name}."
                mods.append(f"{prefix}settings.{stem}")
        if (pkg / "settings.py").exists():
            prefix = "" if pkg == root else f"{pkg.name}."
            mods.append(f"{prefix}settings")
    seen: list[str] = []
    for mod in mods:
        if mod not in seen:
            seen.append(mod)
    return seen


CELERY_FACTORY = re.compile(r"(\w+)\s*=\s*(?:Celery|celery\.Celery)\s*\(")


def _find_celery_app(root: Path) -> str | None:
    """Return the dotted module path for `celery -A <path>`."""
    search_dirs = [root, root / "src", root / "app", root / "backend", root / "config", root / "proj", root / "core"]
    # 1. Модуль, где создаётся объект Celery(...)
    for pkg in search_dirs:
        if not pkg.is_dir():
            continue
        for py in list(pkg.glob("*.py")) + list(pkg.glob("settings/*.py")):
            if not CELERY_FACTORY.search(_read(py)):
                continue
            dotted = ".".join(py.relative_to(root).with_suffix("").parts)
            if dotted.endswith(".__init__"):
                dotted = dotted[: -len(".__init__")]
            return dotted
    # 2. Просто файл celery.py рядом с настройками
    for pkg in search_dirs:
        for candidate in (pkg / "celery.py", pkg / "tasks.py"):
            if candidate.exists():
                return ".".join(candidate.relative_to(root).with_suffix("").parts)
    # 3. Единственный модуль настроек
    mods = _settings_modules(root)
    return mods[0] if len(mods) == 1 else None


def detect(root_str: str) -> dict:
    """Inspect a folder and suggest how to run it."""
    result: dict = {
        "root": root_str,
        "kind": "unknown",
        "interpreter": "",
        "targets": [],
        "env_file": "",
        "start_url": "",
        "admin_url": "",
        "env": {},
        "celery": False,
        "notes": [],
    }
    if not root_str:
        return result

    root = Path(root_str).expanduser()
    if not root.is_dir():
        result["kind"] = "missing"
        result["notes"].append("Папка не найдена")
        return result

    root = root.resolve()
    result["root"] = str(root)
    result["interpreter"] = _venv_python(root) or ""

    for name in (".env", ".env.local", "env.ini"):
        if (root / name).exists():
            result["env_file"] = name
            break

    manager = None
    for candidate in ("manage.py", "manage.pyw"):
        if (root / candidate).exists():
            manager = candidate
            break
    nested_manager = None
    if manager is None:
        for sub in ("src", "app", "backend"):
            if (root / sub / "manage.py").exists():
                nested_manager = f"{sub}/manage.py"
                break

    if manager or nested_manager:
        result["kind"] = "django"
        script = manager or nested_manager
        targets = [
            {
                "name": "web",
                "kind": "web",
                "script": script,
                "args": ["runserver", "127.0.0.1:8000"],
                "autostart": True,
            }
        ]
        celery_app = _find_celery_app(root)
        if celery_app:
            targets.append(
                {
                    "name": "worker",
                    "kind": "worker",
                    "module": "celery",
                    "args": ["-A", celery_app, "worker", "--pool=solo", "--loglevel=info"],
                    "autostart": True,
                }
            )
            targets.append(
                {
                    "name": "beat",
                    "kind": "beat",
                    "module": "celery",
                    "args": ["-A", celery_app, "beat", "--loglevel=info"],
                    "autostart": True,
                }
            )
        result["targets"] = targets
        result["celery"] = bool(celery_app)
        result["start_url"] = "http://127.0.0.1:8000"
        result["admin_url"] = "http://127.0.0.1:8000/admin/"
        result["notes"].append("Обнаружен Django" + (f" (+ celery: {celery_app})" if celery_app else ""))
        return result

    package_json = root / "package.json"
    if package_json.exists():
        result["kind"] = "node"
        import json as _json

        try:
            data = _json.loads(_read(package_json, 40_000) or "{}")
        except ValueError:
            data = {}
        scripts = data.get("scripts") or {}
        targets = []
        entry = next((name for name in ("dev", "start", "serve") if name in scripts), "")
        if entry:
            npm = shutil.which("npm") or "npm"
            targets.append({"name": "web", "kind": "web", "command": [npm, "run", entry], "autostart": True})
        for extra in ("worker", "beat", "dev:worker", "dev:beat"):
            if extra in scripts:
                targets.append(
                    {
                        "name": extra.replace("dev:", "") or extra,
                        "kind": "beat" if "beat" in extra else "worker",
                        "command": [npm, "run", extra],
                        "autostart": True,
                    }
                )
        result["targets"] = targets or [{"name": "app", "kind": "web", "command": ["npm", "start"], "autostart": True}]
        result["start_url"] = "http://127.0.0.1:3000"
        result["notes"].append("Обнаружен Node.js проект")
        return result

    for marker, kind in (("main.py", "fastapi"), ("app.py", "fastapi"), ("run.py", "python")):
        if (root / marker).exists():
            result["kind"] = kind
            break
    if result["kind"] == "unknown" and (root / "asgi.py").exists():
        result["kind"] = "fastapi"
    if result["kind"] == "unknown" and (root / "src").is_dir() and list((root / "src").glob("*.py")):
        result["kind"] = "python"

    if result["kind"] in {"fastapi", "python"}:
        uvicorn_bin = _node_modules_bin(root, ("uvicorn", "gunicorn", "hypercorn"))
        celery_app = _find_celery_app(root)
        targets = []
        if result["kind"] == "fastapi":
            app = _find_asgi_app(root)
            if app:
                exe = uvicorn_bin or "uvicorn"
                targets.append(
                    {
                        "name": "web",
                        "kind": "web",
                        "command": [exe, app, "--reload", "--host", "127.0.0.1", "--port", "8000"],
                        "autostart": True,
                    }
                )
        if not targets:
            entry = "main.py" if (root / "main.py").exists() else "app.py"
            if (root / entry).exists():
                targets.append({"name": "web", "kind": "web", "script": entry, "autostart": True})
        if celery_app:
            targets.append(
                {
                    "name": "worker",
                    "kind": "worker",
                    "module": "celery",
                    "args": ["-A", celery_app, "worker", "--pool=solo", "--loglevel=info"],
                    "autostart": True,
                }
            )
            targets.append(
                {
                    "name": "beat",
                    "kind": "beat",
                    "module": "celery",
                    "args": ["-A", celery_app, "beat", "--loglevel=info"],
                    "autostart": True,
                }
            )
        result["targets"] = targets
        result["celery"] = bool(celery_app)
        if result["kind"] == "fastapi":
            result["start_url"] = "http://127.0.0.1:8000/docs"
        result["notes"].append("Обнаружен Python проект" + (f" (+ celery: {celery_app})" if celery_app else ""))
        return result

    result["kind"] = "unknown"
    result["notes"].append("Тип не определён — укажите команду вручную")
    return result


def _find_asgi_app(root: Path) -> str | None:
    for candidate in ("main:app", "main:application", "app:app", "asgi:application", "src.main:app"):
        module, _, attr = candidate.partition(":")
        path = root / f"{module.replace('.', '/')}.py"
        if path.exists():
            text = _read(path)
            if re.search(rf"\b{attr}\s*=\s*(FastAPI|Flask|Application)\(", text):
                return candidate
    for py in list(root.glob("*.py")) + list((root / "src").glob("*.py")):
        text = _read(py)
        match = re.search(r"(\w+)\s*=\s*(?:FastAPI|Flask)\(", text)
        if match:
            rel = py.relative_to(root).with_suffix("")
            dotted = ".".join(rel.parts)
            return f"{dotted}:{match.group(1)}"
    return None


def default_targets(root_str: str) -> list[dict]:
    info = detect(root_str)
    targets = info.get("targets") or []
    return targets or [{"name": "app", "kind": "web", "script": "", "args": [], "autostart": False}]
