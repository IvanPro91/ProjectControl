from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from projectcontrol.bus import EventBus  # noqa: E402
from projectcontrol.models import Project, Target, effective_interpreter, env_paths  # noqa: E402
from projectcontrol.runner import ProjectRunner, resolve_executable  # noqa: E402


def _wait(predicate, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


# --------------------------------------------------------------- окружения
def _make_venv(root: Path) -> tuple[str, str]:
    """Папка, которая выглядит как venv, и путь к python.exe внутри неё."""
    venv = root / ".venv"
    exe = venv / "Scripts" / "python.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("", encoding="utf-8")
    return str(venv), str(exe)


def test_env_paths_from_folder(tmp_path: Path) -> None:
    venv, exe = _make_venv(tmp_path)
    assert env_paths(venv) == (venv, exe)


def test_env_paths_from_python_exe(tmp_path: Path) -> None:
    venv, exe = _make_venv(tmp_path)
    assert env_paths(exe) == (venv, exe)


def test_env_paths_from_plain_interpreter(tmp_path: Path) -> None:
    assert env_paths(r"C:\Python311\python.exe") == ("", r"C:\Python311\python.exe")


def test_env_paths_empty_means_no_activation() -> None:
    assert env_paths("") == ("", "")


def test_priority_process_project_global(tmp_path: Path) -> None:
    project = Project(root=str(tmp_path), interpreter="", targets=[])
    assert effective_interpreter(project, None, "") == ""
    assert effective_interpreter(project, None, "C:/global/.venv") == "C:/global/.venv"
    project.interpreter = "C:/project/.venv"
    assert effective_interpreter(project, None, "C:/global/.venv") == "C:/project/.venv"
    target = Target(name="w", interpreter="C:/target/.venv")
    assert effective_interpreter(project, target, "C:/global/.venv") == "C:/target/.venv"


def test_venv_wins_over_path(tmp_path: Path) -> None:
    venv, exe = _make_venv(tmp_path)
    project = Project(root=str(tmp_path), interpreter=venv, targets=[])
    target = Target(name="app", command=["python", "main.py"])
    argv, error, note = resolve_executable(target.argv(project), project, target)
    assert error == ""
    assert argv == [exe, "main.py"]
    assert "venv" in note


def test_venv_celery_is_used_before_path(tmp_path: Path) -> None:
    venv, _exe = _make_venv(tmp_path)
    celery = Path(venv) / "Scripts" / "celery.exe"
    celery.write_text("", encoding="utf-8")
    project = Project(root=str(tmp_path), interpreter=venv, targets=[])
    target = Target(name="worker", command=["celery", "-A", "x", "worker"])
    argv, error, _note = resolve_executable(target.argv(project), project, target)
    assert error == ""
    assert argv[0] == str(celery)


def test_celery_falls_back_to_module_inside_venv(tmp_path: Path) -> None:
    venv, exe = _make_venv(tmp_path)
    project = Project(root=str(tmp_path), interpreter=venv, targets=[])
    target = Target(name="worker", command=["celery", "-A", "x", "worker"])
    argv, error, note = resolve_executable(target.argv(project), project, target)
    assert error == ""
    assert argv == [exe, "-m", "celery", "-A", "x", "worker"]
    assert "не найден" in note


def test_no_venv_no_interpreter_is_clear_error(tmp_path: Path, monkeypatch) -> None:
    import shutil as _shutil

    monkeypatch.setattr(_shutil, "which", lambda *a, **k: None)
    project = Project(root=str(tmp_path), interpreter="", targets=[])
    target = Target(name="app", command=["python", "main.py"])
    argv, error, note = resolve_executable(target.argv(project), project, target)
    assert error
    assert not note


def test_runner_activates_venv_in_env(tmp_path: Path) -> None:
    venv, _exe = _make_venv(tmp_path)
    project = Project(root=str(tmp_path), interpreter=venv, targets=[])
    target = Target(name="app", command=["python", "-c", "pass"])
    runner = ProjectRunner(EventBus(), default_venv=venv)
    env = runner._build_env(project, target)
    assert env["VIRTUAL_ENV"] == venv
    assert env["PATH"].split(os.pathsep)[0].endswith(("Scripts", "bin"))
    assert "PYTHONHOME" not in env
    runner.shutdown()


def test_runner_uses_global_venv_for_all_projects(tmp_path: Path) -> None:
    venv, exe = _make_venv(tmp_path)
    runner = ProjectRunner(EventBus(), default_venv=venv)
    a = Project(root=str(tmp_path), interpreter="", targets=[])
    b = Project(root=str(tmp_path), interpreter="", targets=[])
    for project in (a, b):
        target = Target(name="app", command=["python", "main.py"])
        project.targets = [target]
        argv, error, _ = resolve_executable(target.argv(project), project, target, runner.global_venv())
        assert error == ""
        assert argv[0] == exe
    runner.shutdown()


def test_no_venv_means_no_activation(tmp_path: Path) -> None:
    project = Project(root=str(tmp_path), interpreter="", targets=[])
    target = Target(name="app", command=["python", "-c", "pass"])
    runner = ProjectRunner(EventBus(), default_venv="")
    env = runner._build_env(project, target)
    assert "VIRTUAL_ENV" not in env
    runner.shutdown()


# --------------------------------------------------------- автоперезапуск
def test_autorestart_delay_is_constant_ten_seconds(tmp_path: Path) -> None:
    runner = ProjectRunner(EventBus(), log_lines=500)
    project = Project(root=str(tmp_path), interpreter=sys.executable, targets=[])
    target = Target(
        name="flaky", kind="task",
        args=["-c", "raise SystemExit(1)"],
        autorestart=True, restart_delay=10.0, stop_timeout=0.2,
    )
    project.targets = [target]
    runner.start(project, target)

    def started_twice() -> bool:
        return len([e for e in runner.tail(project.id)[0] if e.text.startswith("$ ")]) >= 2

    assert _wait(started_twice, timeout=25), [e.text for e in runner.tail(project.id)[0]][-5:]
    runner.stop(project, target, force=True)
    runner.shutdown()


def test_autorestart_can_be_disabled(tmp_path: Path) -> None:
    runner = ProjectRunner(EventBus(), log_lines=500)
    project = Project(root=str(tmp_path), interpreter=sys.executable, targets=[])
    target = Target(name="once", kind="task", args=["-c", "raise SystemExit(1)"],
                    autorestart=False, stop_timeout=0.2)
    project.targets = [target]
    runner.start(project, target)
    time.sleep(1.5)
    launches = len([e for e in runner.tail(project.id)[0] if e.text.startswith("$ ")])
    assert launches == 1, launches
    runner.shutdown()


def test_default_restart_delay_is_ten() -> None:
    assert Target(name="x").restart_delay == 10.0
    assert Project(root="C:/a").restart_delay == 10.0
