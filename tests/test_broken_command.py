"""Регрессия: папка .venv попадала в argv[0] вместо интерпретатора.

Именно это ломало celery:
    $ C:\\Projects\\app\\.venv\\ -m celery -A ... worker
    Не удалось запустить: [WinError 5] Отказано в доступе
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from projectcontrol.bus import EventBus  # noqa: E402
from projectcontrol.models import Project, Target  # noqa: E402
from projectcontrol.runner import ProjectRunner, resolve_executable  # noqa: E402


def _make_venv(root: Path) -> tuple[str, str]:
    venv = root / ".venv"
    exe = venv / "Scripts" / "python.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("", encoding="utf-8")
    return str(venv), str(exe)


def test_directory_as_argv0_is_replaced_by_interpreter(tmp_path: Path) -> None:
    """Старая сломанная команда '.venv\\ -m celery' должна починиться сама."""
    venv, exe = _make_venv(tmp_path)
    project = Project(root=str(tmp_path), interpreter=venv, targets=[])
    target = Target(name="worker", kind="worker",
                    command=[venv + "\\", "-m", "celery", "-A", "src.settings.celery", "worker"])
    argv, error, note = resolve_executable(target.argv(project), project, target)
    assert error == "", error
    assert argv == [exe, "-m", "celery", "-A", "src.settings.celery", "worker"], argv
    assert "папка окружения" in note


def test_directory_without_python_reports_error(tmp_path: Path) -> None:
    plain = tmp_path / "some_folder"
    plain.mkdir()
    project = Project(root=str(tmp_path), interpreter="", targets=[])
    target = Target(name="app", command=[str(plain)])
    argv, error, note = resolve_executable(target.argv(project), project, target)
    assert error
    assert not note
    assert "папка" in error.lower()


def test_bare_directory_without_venv_never_popen(tmp_path: Path) -> None:
    """Даже если папка не похожа на venv — запускать её нельзя, будет WinError 5."""
    plain = tmp_path / "not_a_venv"
    plain.mkdir()
    project = Project(root=str(tmp_path), interpreter="", targets=[])
    target = Target(name="app", command=[str(plain), "-c", "print(1)"])
    argv, error, _note = resolve_executable(target.argv(project), project, target)
    assert error, argv


def test_python_placeholder_uses_venv_python(tmp_path: Path) -> None:
    venv, exe = _make_venv(tmp_path)
    project = Project(root=str(tmp_path), interpreter=venv, targets=[])
    for command in (
        ["{python}", "-m", "celery", "-A", "x", "worker", "--pool=solo"],
        ["{python}", "manage.py", "runserver"],
    ):
        target = Target(name="t", command=command)
        argv, error, _ = resolve_executable(target.argv(project), project, target)
        assert error == ""
        assert argv[0] == exe, argv


def test_python_token_resolves_even_without_trailing_script(tmp_path: Path) -> None:
    venv, exe = _make_venv(tmp_path)
    project = Project(root=str(tmp_path), interpreter=venv + "\\", targets=[])
    target = Target(name="t", command=["{python}", "-c", "import sys;print(sys.executable)"])
    argv, error, _ = resolve_executable(target.argv(project), project, target)
    assert error == ""
    assert argv[0] == exe


def test_runner_start_with_broken_command_recovers(tmp_path: Path) -> None:
    """Сохранённый сломанный проект стартует после самопочинки."""
    import shutil as _shutil

    venv = tmp_path / ".venv"
    exe = venv / "Scripts" / "python.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    _shutil.copy2(sys.executable, exe)  # настоящий интерпретатор внутри «venv»

    project = Project(root=str(tmp_path), interpreter=str(venv), targets=[])
    target = Target(name="t", kind="task",
                    command=[str(venv) + "\\", sys.executable, "-c", "print('fixed')"],
                    autorestart=False, stop_timeout=0.2)
    project.targets = [target]

    runner = ProjectRunner(EventBus(), log_lines=200)
    result = runner.start(project, target)
    assert result["ok"] is True, result
    deadline = time.monotonic() + 15
    texts = []
    while time.monotonic() < deadline:
        texts = [e.text for e in runner.tail(project.id)[0]]
        if any("fixed" in t for t in texts):
            break
        time.sleep(0.1)
    assert any("fixed" in t for t in texts), texts
    assert not any("Отказано в доступе" in t for t in texts), texts
    assert any(t.startswith("$ ") and "python.exe" in t for t in texts), texts
    runner.shutdown()
