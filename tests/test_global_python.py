from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from projectcontrol.bus import EventBus  # noqa: E402
from projectcontrol.detect import strip_ansi  # noqa: E402
from projectcontrol.models import Project, Target  # noqa: E402
from projectcontrol.runner import ProjectRunner, resolve_executable  # noqa: E402


def _wait(predicate, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_global_python_command_uses_path_python(tmp_path: Path) -> None:
    """`python app.py` должно запускать глобальный python из PATH, а не venv панели."""
    global_python = shutil.which("python")
    if not global_python:
        pytest.skip("глобальный python не найден в PATH")

    (tmp_path / "app.py").write_text(
        "import sys\nprint('ran by', sys.executable, flush=True)\n", encoding="utf-8"
    )
    project = Project(root=str(tmp_path), interpreter="", targets=[])
    target = Target(name="app", kind="web", command=["python", "app.py"])
    project.targets = [target]

    argv, error, _note = resolve_executable(target.argv(project), project, target)
    assert error == "", error
    assert Path(argv[0]).resolve() == Path(global_python).resolve(), argv
    assert argv[1:] == ["app.py"]

    runner = ProjectRunner(EventBus(), log_lines=200)
    result = runner.start(project, target)
    assert result["ok"] is True, result
    assert _wait(lambda: any(
        "ran by" in e.text for e in runner.tail(project.id)[0]))
    texts = [strip_ansi(e.text) for e in runner.tail(project.id)[0]]
    assert any(global_python.lower() in t.lower() for t in texts), texts
    runner.shutdown()


def test_python_is_replaced_by_project_interpreter_when_not_in_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    project = Project(root=str(tmp_path), interpreter=sys.executable, targets=[])
    target = Target(name="app", command=["python", "app.py"])
    argv, error, _note = resolve_executable(target.argv(project), project, target)
    assert error == ""
    assert argv == [sys.executable, "app.py"]

    # без интерпретатора проекта — понятная ошибка, а не запуск REPL
    project2 = Project(root=str(tmp_path), interpreter="", targets=[])
    _, error2, _n2 = resolve_executable(target.argv(project2), project2, target)
    assert error2
    assert "PATH" in error2


def test_missing_interpreter_is_reported(tmp_path: Path) -> None:
    project = Project(root=str(tmp_path), interpreter="", targets=[])
    target = Target(name="app", command=["python3.13-does-not-exist", "app.py"])
    argv, error, _note = resolve_executable(target.argv(project), project, target)
    assert error
    assert "не найден" in error


def test_script_mode_uses_project_interpreter(tmp_path: Path) -> None:
    """Режим «Скрипт» всегда берёт интерпретатор проекта (как в IDE)."""
    project = Project(root=str(tmp_path), interpreter=sys.executable, targets=[])
    target = Target(name="app", script="app.py", args=["--debug"])
    argv, error, _note = resolve_executable(target.argv(project), project, target)
    assert error == ""
    assert argv[0] == sys.executable
    assert argv[1].endswith("app.py")
    assert argv[2] == "--debug"


def test_missing_interpreter_path_is_reported(tmp_path: Path) -> None:
    project = Project(root=str(tmp_path), interpreter=str(tmp_path / "no" / "python.exe"), targets=[])
    target = Target(name="app", script="app.py")
    _, error, _note = resolve_executable(target.argv(project), project, target)
    assert error
    assert "не найден" in error
