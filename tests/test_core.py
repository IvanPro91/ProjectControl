from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from projectcontrol import detect  # noqa: E402
from projectcontrol.models import Project, Target  # noqa: E402
from projectcontrol.runner import resolve_executable  # noqa: E402


def test_script_target_argv(tmp_path: Path) -> None:
    project = Project(root=str(tmp_path), interpreter="py", targets=[])
    target = Target(name="web", kind="web", script="manage.py", args=["runserver", "127.0.0.1:8000"])
    assert target.argv(project) == ["py", str(tmp_path / "manage.py"), "runserver", "127.0.0.1:8000"]


def test_module_target_argv() -> None:
    project = Project(root="C:/app", interpreter="py", targets=[])
    target = Target(name="worker", kind="worker", module="celery", args=["-A", "config.celery", "worker", "--pool=solo"])
    assert target.argv(project) == ["py", "-m", "celery", "-A", "config.celery", "worker", "--pool=solo"]


def test_command_target_argv_and_tokens() -> None:
    project = Project(root="C:/app", name="demo", host="0.0.0.0", port=9000, targets=[])
    target = Target(name="beat", kind="beat", command=["celery", "-A", "config.celery", "beat", "--port={port}"])
    assert target.argv(project)[-1] == "--port=9000"


def test_workdir_relative_to_root(tmp_path: Path) -> None:
    project = Project(root=str(tmp_path), targets=[])
    target = Target(name="web", cwd="backend")
    assert target.workdir(project) == str((tmp_path / "backend").resolve())


def test_resolve_celery_falls_back_to_interpreter() -> None:
    project = Project(root="C:/app", interpreter="C:/app/.venv/Scripts/python.exe", targets=[])
    target = Target(name="worker", command=["celery", "-A", "config.celery", "worker"])
    argv, error, _note = resolve_executable(target.argv(project), project, target)
    assert error == ""
    assert argv[0].replace("\\", "/") == "C:/app/.venv/Scripts/python.exe"
    assert argv[1:3] == ["-m", "celery"]


def test_resolve_missing_binary_reports_error() -> None:
    project = Project(root="C:/app", targets=[])
    target = Target(name="web", command=["definitely-not-a-real-binary-xyz"])
    argv, error, _note = resolve_executable(target.argv(project), project, target)
    assert error
    assert "не найден" in error


def test_log_lines_clamped() -> None:
    assert Project(root="C:/app", log_lines=1).log_lines == 100
    assert Project(root="C:/app", log_lines=10 ** 9).log_lines == 200000


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_detect_django_with_celery(tmp_path: Path) -> None:
    _write(tmp_path / "manage.py", "import django\n")
    _write(tmp_path / "src" / "settings" / "__init__.py", "")
    _write(
        tmp_path / "src" / "settings" / "celery.py",
        'from celery import Celery\napp = Celery("shop")\n',
    )
    info = detect.detect(str(tmp_path))
    assert info["kind"] == "django"
    assert info["admin_url"].endswith("/admin/")
    names = [t["name"] for t in info["targets"]]
    assert names == ["web", "worker", "beat"]
    worker = info["targets"][1]
    assert worker["module"] == "celery"
    assert worker["args"][:2] == ["-A", "src.settings.celery"]


def test_detect_fastapi(tmp_path: Path) -> None:
    _write(tmp_path / "main.py", "from fastapi import FastAPI\napp = FastAPI()\n")
    info = detect.detect(str(tmp_path))
    assert info["kind"] == "fastapi"
    assert info["targets"][0]["command"][0] == "uvicorn"
    assert info["targets"][0]["command"][1] == "main:app"


def test_detect_missing_folder() -> None:
    assert detect.detect("Z:/nope/nope")["kind"] == "missing"


def test_detect_venv_python(tmp_path: Path) -> None:
    exe = tmp_path / ".venv" / "Scripts" / "python.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    _write(tmp_path / "main.py", "print(1)\n")
    info = detect.detect(str(tmp_path))
    assert info["interpreter"] == str(exe)
