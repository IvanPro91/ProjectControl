from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from .bus import EventBus
from .config import DEFAULT_LOG_LINES
from .detect import strip_ansi
from .logs import LogEntry, RingLog
from .models import Project, Target, effective_interpreter, env_paths

WIN = sys.platform == "win32"

if WIN:
    CREATE_NEW_PROCESS_GROUP = 0x00000200

BIN_DIR = "Scripts" if os.name == "nt" else "bin"
EXE_NAME = "python.exe" if os.name == "nt" else "python"
EXE_SUFFIXES = (".exe", ".cmd", ".bat", "") if os.name == "nt" else ("",)


class RunningTarget:
    __slots__ = ("process", "target", "started_at", "log", "pump", "watcher", "stopping", "exit_code", "restarts")

    def __init__(self, process: subprocess.Popen, target: Target, log: RingLog):
        self.process = process
        self.target = target
        self.started_at = time.time()
        self.log = log
        self.pump: threading.Thread | None = None
        self.watcher: threading.Thread | None = None
        self.stopping = False
        self.exit_code: int | None = None
        self.restarts = 0

    @property
    def pid(self) -> int:
        return self.process.pid

    def alive(self) -> bool:
        return self.process.poll() is None

    def uptime(self) -> float:
        return max(0.0, time.time() - self.started_at)


class ProjectRunner:
    """Runs one or more processes per project and streams their output."""

    def __init__(self, bus: EventBus, log_lines: int = DEFAULT_LOG_LINES, default_venv: str | None = None):
        self.bus = bus
        self.default_log_lines = log_lines
        self._default_venv = default_venv if callable(default_venv) else (lambda: default_venv or "")
        self._lock = threading.RLock()
        self._running: dict[str, dict[str, RunningTarget]] = {}
        self._logs: dict[str, RingLog] = {}
        self._restart_pending: dict[str, threading.Timer] = {}
        self._on_change = None

    def set_default_venv(self, value: str) -> None:
        self._default_venv = lambda: value or ""

    def global_venv(self) -> str:
        return self._default_venv() or ""

    # ---- окружение -------------------------------------------------
    def venv_of(self, project: Project, target: Target | None = None) -> tuple[str, str]:
        """(папка venv, python) с учётом приоритета: процесс -> проект -> общая настройка."""
        return env_paths(effective_interpreter(project, target, self.global_venv()))


    # ---- helpers -----------------------------------------------------
    def log_for(self, project_id: str, maxlen: int | None = None) -> RingLog:
        with self._lock:
            log = self._logs.get(project_id)
            if log is None:
                log = RingLog(maxlen or self.default_log_lines)
                self._logs[project_id] = log
            elif maxlen:
                log.set_maxlen(maxlen)
            return log

    def set_maxlen(self, project_id: str, maxlen: int) -> None:
        with self._lock:
            log = self._logs.get(project_id)
            if log is not None:
                log.set_maxlen(maxlen)

    def change_hook(self, hook) -> None:
        self._on_change = hook

    def _emit(self, event: dict) -> None:
        self.bus.publish(event)
        if self._on_change:
            try:
                self._on_change(event)
            except Exception:
                pass

    def _env_file_vars(self, project: Project) -> dict[str, str]:
        if not project.load_env_file or not project.env_file:
            return {}
        path = Path(project.env_file)
        if not path.is_absolute():
            path = Path(project.root) / project.env_file
        if not path.exists():
            return {}
        result: dict[str, str] = {}
        try:
            for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip().removeprefix("export ").strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    result[key] = value
        except OSError:
            return {}
        return result

    def _build_env(self, project: Project, target: Target) -> dict[str, str]:
        env = dict(os.environ)
        venv, _python = self.venv_of(project, target)
        if venv:
            scripts = Path(venv) / BIN_DIR
            env["VIRTUAL_ENV"] = venv
            env["PATH"] = str(scripts) + os.pathsep + env.get("PATH", "")
            env.pop("PYTHONHOME", None)
            env.pop("PYTHONPATH", None)
        env.update(self._env_file_vars(project))
        env.update({k: v for k, v in project.env.items() if k})
        env.update({k: v for k, v in target.env.items() if k})
        env["PYTHONUNBUFFERED"] = "1"
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env["PROJECTCONTROL_TARGET"] = target.name
        env["PROJECTCONTROL_PROJECT"] = project.name
        return env

    # ---- lifecycle ---------------------------------------------------
    def start(self, project: Project, target: Target, auto: bool = False) -> dict:
        with self._lock:
            bucket = self._running.setdefault(project.id, {})
            existing = bucket.get(target.id)
            if existing and existing.alive():
                return {"ok": True, "already_running": True, "pid": existing.pid}

        if not (target.command or target.script or target.module or target.args):
            return {
                "ok": False,
                "error": (
                    f"Процесс «{target.name}»: не задана команда запуска. "
                    "Укажи скрипт, модуль (-m) или аргументы — просто открывать интерпретатор нельзя."
                ),
            }

        argv = [str(a) for a in target.argv(project)]
        if not argv or not argv[0].strip():
            return {"ok": False, "error": "Пустая команда запуска"}
        original = argv[0]

        cwd = target.workdir(project)
        if cwd and not Path(cwd).is_dir():
            return {"ok": False, "error": f"Рабочая папка не найдена: {cwd}"}

        argv, error, note = resolve_executable(argv, project, target, self.global_venv())
        if error:
            return {"ok": False, "error": error}

        log = self.log_for(project.id, project.log_lines)
        venv, _python = self.venv_of(project, target)
        if venv:
            self._log_line(project, target, target.name, f"окружение: {venv}", "meta")
        self._log_line(project, target, target.name, f"$ {' '.join(argv)}", "cmd")
        if note:
            self._log_line(project, target, target.name, note, "meta")
        popen_kwargs: dict = {
            "cwd": cwd or None,
            "env": self._build_env(project, target),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
            "bufsize": 0,
        }
        if WIN:
            popen_kwargs["creationflags"] = CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(argv, **popen_kwargs)  # noqa: S603
        except (OSError, ValueError) as exc:
            self._log_line(project, target, target.name, f"Не удалось запустить: {exc}", "error")
            return {"ok": False, "error": str(exc)}

        running = RunningTarget(process, target, log)
        with self._lock:
            self._running.setdefault(project.id, {})[target.id] = running
        pump = threading.Thread(
            target=self._start_pump, args=(project, running), name=f"pc-out-{project.name}-{target.name}", daemon=True
        )
        running.pump = pump
        pump.start()
        watcher = threading.Thread(
            target=self._watch, args=(project, running), name=f"pc-wait-{project.name}-{target.name}", daemon=True
        )
        running.watcher = watcher
        watcher.start()
        self._emit({"type": "status", "project_id": project.id, "target_id": target.id, "status": self.status(project, target)})
        return {"ok": True, "pid": process.pid}

    def _start_pump(self, project: Project, running: RunningTarget) -> None:
        stream = running.process.stdout
        if stream is None:
            return
        buffer = b""
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    raw, _, buffer = buffer.partition(b"\n")
                    self._log_line(project, running.target, running.target.name, raw.decode("utf-8", "replace"))
        except (OSError, ValueError):
            pass
        finally:
            rest = buffer.decode("utf-8", "replace").rstrip("\r")
            if rest:
                self._log_line(project, running.target, running.target.name, rest)
            try:
                stream.close()
            except OSError:
                pass

    def _watch(self, project: Project, running: RunningTarget) -> None:
        exit_code = running.process.wait()
        running.exit_code = exit_code
        if running.pump and running.pump.is_alive():
            running.pump.join(timeout=3)
        with self._lock:
            bucket = self._running.get(project.id, {})
            if bucket.get(running.target.id) is running:
                del bucket[running.target.id]
        if running.stopping:
            self._log_line(project, running.target, running.target.name, f"Остановлен (код {exit_code})", "meta")
        else:
            self._log_line(
                project,
                running.target,
                running.target.name,
                f"Процесс завершился с кодом {exit_code}",
                "error" if exit_code else "meta",
            )
        if running.target.autorestart and not running.stopping:
            self._schedule_restart(project, running.target, running.restarts + 1)
        self._emit({"type": "status", "project_id": project.id, "target_id": running.target.id, "status": self.status(project, running.target)})

    def _schedule_restart(self, project: Project, target: Target, attempt: int) -> None:
        delay = target.restart_delay if target.restart_delay and target.restart_delay > 0 else 10.0
        key = f"{project.id}:{target.id}"
        with self._lock:
            existing = self._restart_pending.get(key)
            if existing:
                existing.cancel()
            timer = threading.Timer(delay, self._auto_restart, args=(project, target, attempt))
            timer.daemon = True
            self._restart_pending[key] = timer
        timer.start()

    def _auto_restart(self, project: Project, target: Target, attempt: int) -> None:
        with self._lock:
            self._restart_pending.pop(f"{project.id}:{target.id}", None)
        fresh = project.target(target.id) or target
        result = self.start(project, fresh, auto=True)
        if result.get("ok"):
            with self._lock:
                bucket = self._running.get(project.id, {})
                handle = bucket.get(fresh.id)
                if handle:
                    handle.restarts = attempt

    def stop(self, project: Project, target: Target, force: bool = False) -> dict:
        with self._lock:
            timer = self._restart_pending.pop(f"{project.id}:{target.id}", None)
            if timer:
                timer.cancel()
            running = self._running.get(project.id, {}).get(target.id)
        if running is None or not running.alive():
            self._log_line(project, target, target.name, "Процесс не запущен", "meta")
            return {"ok": True, "stopped": False}
        running.stopping = True
        self._terminate(running, force or target.stop_timeout <= 0)
        return {"ok": True, "stopped": True, "pid": running.pid}

    def _terminate(self, running: RunningTarget, force: bool) -> None:
        pid = running.pid
        if force:
            self._hard_kill(pid)
            return
        timeout = max(0.5, running.target.stop_timeout)
        try:
            if WIN:
                running.process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, ValueError, ProcessLookupError):
            pass
        try:
            running.process.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            pass
        self._hard_kill(pid)

    @staticmethod
    def _hard_kill(pid: int) -> None:
        if WIN:
            try:
                subprocess.run(  # noqa: S603
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                pass
            return
        for sig in (signal.SIGKILL, signal.SIGTERM):
            try:
                os.killpg(os.getpgid(pid), sig)
                return
            except (OSError, ProcessLookupError):
                continue

    def stop_project(self, project: Project) -> list[dict]:
        return [self.stop(project, target) for target in project.targets]

    def status(self, project: Project, target: Target) -> dict:
        with self._lock:
            running = self._running.get(project.id, {}).get(target.id)
        if running and running.alive():
            return {
                "state": "running",
                "pid": running.pid,
                "uptime": running.uptime(),
                "restarts": running.restarts,
                "started_at": running.started_at,
            }
        if running:
            return {"state": "stopping", "pid": running.pid, "uptime": running.uptime()}
        return {"state": "stopped", "pid": None, "uptime": 0.0, "restarts": 0}

    def snapshot(self, project: Project) -> dict[str, dict]:
        return {target.id: self.status(project, target) for target in project.targets}

    def is_running(self, project_id: str) -> bool:
        with self._lock:
            return any(handle.alive() for handle in self._running.get(project_id, {}).values())

    def shutdown(self) -> None:
        with self._lock:
            timers = list(self._restart_pending.values())
            handles = [h for bucket in self._running.values() for h in bucket.values()]
            self._restart_pending.clear()
        for timer in timers:
            timer.cancel()
        for handle in handles:
            handle.stopping = True
            self._hard_kill(handle.pid)

    # ---- logs --------------------------------------------------------
    def _log_line(self, project: Project, target: Target, target_name: str, text: str, stream: str = "stdout") -> LogEntry:
        text = strip_ansi(text).rstrip("\r")
        log = self.log_for(project.id, project.log_lines)
        entry = log.append(text, target.id, target_name or target.name, stream)
        self.bus.publish({"type": "log", "project_id": project.id, "entry": entry.as_dict()})
        return entry

    def tail(self, project_id: str, since: int = 0, limit: int = 800, maxlen: int | None = None) -> tuple[list[LogEntry], int]:
        log = self.log_for(project_id, maxlen)
        return log.tail(since=since, limit=limit)


def _looks_like_path(value: str) -> bool:
    return "/" in value or "\\" in value


def resolve_executable(
    argv: list[str], project: Project, target: Target, default_venv: str = ""
) -> tuple[list[str], str, str]:
    """Подбирает исполняемый файл. Возвращает (argv, ошибка, пояснение для лога).

    Порядок: явный путь -> исполняемый файл внутри venv -> PATH -> интерпретатор проекта.
    """
    if not argv:
        return argv, "Пустая команда запуска", ""
    executable = argv[0].strip()
    name = Path(executable).name.lower()
    interpreter = effective_interpreter(project, target, default_venv)
    venv, python = env_paths(interpreter)

    # 0. argv[0] оказался папкой окружения (частая ошибка: ".venv\ -m celery")
    if Path(executable).is_dir():
        dir_venv, dir_python = env_paths(executable)
        if dir_python:
            return ([dir_python, *argv[1:]], "",
                    f"«{executable}» — папка окружения, запуск через {dir_python}")
        return argv, f"«{executable}» — это папка, а не исполняемый файл", ""

    if Path(executable).exists():
        return [executable, *argv[1:]], "", ""

    # 1. то же самое, но внутри venv (celery.exe, uvicorn.exe, python.exe ...)
    if venv:
        scripts = Path(venv) / BIN_DIR
        for suffix in EXE_SUFFIXES:
            candidate = scripts / (executable + suffix)
            if candidate.exists():
                return [str(candidate), *argv[1:]], "", f"«{executable}» -> «{candidate}» (из venv)"

    # 2. системный PATH (на Windows shutil.which сам учитывает PATHEXT)
    found = shutil.which(executable)
    if found:
        return [found, *argv[1:]], "", ""

    # 3. celery нет ни в venv, ни в PATH -> запускаем как модуль интерпретатора
    if name.startswith("celery"):
        if python:
            return [python, "-m", "celery", *argv[1:]], "", f"celery не найден, запуск: {python} -m celery"
        return argv, (
            "Celery не найден в venv и в PATH. Укажи окружение проекта (.venv) в настройках "
            "или добавь celery в PATH."
        ), ""

    # 4. python / py -> интерпретатор окружения
    if name in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}:
        if python:
            if _looks_like_path(python) and not Path(python).exists():
                return argv, f"Интерпретатор не найден: {python}", ""
            return [python, *argv[1:]], "", f"«{executable}» -> «{python}» (интерпретатор окружения)"
        return argv, (
            f"Python не найден в PATH ({executable}) и не задано окружение проекта. "
            "Укажи .venv в настройках."
        ), ""

    if python and Path(python).name.lower() == name:
        return argv, f"Интерпретатор не найден: {python}", ""

    return argv, f"Исполняемый файл не найден: {executable}", ""

