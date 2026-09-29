from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from projectcontrol.bus import EventBus  # noqa: E402
from projectcontrol.logs import LineSplitter, RingLog  # noqa: E402
from projectcontrol.models import Project, Target  # noqa: E402
from projectcontrol.runner import ProjectRunner  # noqa: E402
from projectcontrol.store import Store  # noqa: E402


# --------------------------------------------------------------------- logs
def test_ringlog_keeps_last_lines() -> None:
    log = RingLog(maxlen=5)
    for i in range(20):
        log.append(f"line {i}")
    entries, dropped = log.tail()
    assert [e.text for e in entries] == [f"line {i}" for i in range(15, 20)]
    assert dropped == 15
    assert log.stats()["kept"] == 5


def test_ringlog_tail_since() -> None:
    log = RingLog(maxlen=100)
    for i in range(10):
        log.append(str(i))
    last = log.tail(limit=1)[0][-1]
    entries, _ = log.tail(since=last.seq - 2)
    assert [e.text for e in entries] == ["8", "9"]
    assert log.tail(since=last.seq)[0] == []


def test_ringlog_resize_keeps_newest() -> None:
    log = RingLog(maxlen=50)
    for i in range(40):
        log.append(str(i))
    log.set_maxlen(10)
    entries, _ = log.tail()
    assert len(entries) == 10
    assert entries[-1].text == "39"


def test_line_splitter() -> None:
    splitter = LineSplitter()
    assert splitter.feed("a\nb\nc") == ["a", "b"]
    assert splitter.feed("d\n") == ["cd"]
    assert splitter.flush() == []
    assert LineSplitter().feed("no newline") == []
    assert LineSplitter().feed("tail") == []
    s2 = LineSplitter()
    s2.feed("x")
    assert s2.flush() == ["x"]


# -------------------------------------------------------------------- store
def test_store_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "db.json"
    store = Store(db)
    project = Project(
        root=str(tmp_path),
        name="demo",
        interpreter="py",
        targets=[Target(name="worker", kind="worker", command=["celery", "-A", "x", "worker"])],
    )
    store.add(project)
    assert store.history[0] == str(tmp_path)

    reloaded = Store(db)
    saved = reloaded.get(project.id)
    assert saved is not None
    assert saved.targets[0].command == ["celery", "-A", "x", "worker"]
    assert reloaded.templates  # builtin templates always available


def test_store_broken_file_is_recovered(tmp_path: Path) -> None:
    db = tmp_path / "db.json"
    db.write_text("{ not json", encoding="utf-8")
    store = Store(db)
    assert store.projects == []
    assert any(t.name == "Django + Celery (worker + beat)" for t in store.templates)


# ------------------------------------------------------------------- runner
def _wait(predicate, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.mark.skipif(sys.platform != "win32", reason="использует python.exe проекта")
def test_runner_start_stop_and_logs(tmp_path: Path) -> None:
    import sys as _sys

    script = tmp_path / "worker.py"
    script.write_text(
        "import time\nfor i in range(5):\n    print('tick', i, flush=True)\ntime.sleep(30)\n",
        encoding="utf-8",
    )
    bus = EventBus()
    runner = ProjectRunner(bus, log_lines=50)
    project = Project(root=str(tmp_path), interpreter=_sys.executable, targets=[])
    target = Target(name="worker", kind="worker", script="worker.py")
    project.targets = [target]

    result = runner.start(project, target)
    assert result["ok"], result
    assert _wait(lambda: runner.status(project, target)["state"] == "running")
    assert _wait(lambda: any(e.text == "tick 4" for e in runner.tail(project.id)[0]))

    stopped = runner.stop(project, target)
    assert stopped["ok"]
    assert _wait(lambda: runner.status(project, target)["state"] == "stopped")

    log = runner.log_for(project.id)
    for ch in ("a", "b", "c"):
        log.append(ch)
    log.set_maxlen(2)
    assert [e.text for e in log.tail()[0]] == ["b", "c"]
    runner.shutdown()


def test_runner_log_limit_keeps_memory_bounded(tmp_path: Path) -> None:
    runner = ProjectRunner(EventBus(), log_lines=100)
    log = runner.log_for("p1")
    for i in range(5000):
        log.append(str(i))
    entries, dropped = runner.tail("p1")
    assert len(entries) == 100
    assert dropped == 4900
    runner.shutdown()


def test_runner_reports_missing_binary(tmp_path: Path) -> None:
    runner = ProjectRunner(EventBus())
    project = Project(root=str(tmp_path), targets=[])
    target = Target(name="x", command=["totally-missing-binary-xyz"])
    project.targets = [target]
    result = runner.start(project, target)
    assert result["ok"] is False
    assert "не найден" in result["error"]
    runner.shutdown()
