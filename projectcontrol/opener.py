from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import webbrowser
from pathlib import Path

WIN = sys.platform == "win32"

IDE_ALIASES = {
    "vscode": "code",
    "code": "code",
    "pycharm": "pycharm64",
    "pycharm64": "pycharm64",
    "idea": "idea64",
    "idea64": "idea64",
    "pycharm-community": "pycharm64",
    "webstorm": "webstorm64",
    "sublime": "subl",
    "vim": "vim",
    "notepad": "notepad",
}

TOKEN = re.compile(r"\{([a-z_]+)\}")


def split_command(command: str) -> list[str]:
    command = command.strip()
    if not command:
        return []
    if WIN:
        return [part for part in shlex.split(command, posix=False) if part]
    return shlex.split(command)


def expand(command: str, root: str) -> str:
    values = {"root": root, "path": root, "dir": root, "cwd": root}
    return TOKEN.sub(lambda m: values.get(m.group(1), m.group(0)), command)


def open_folder(path: str, select: bool = False) -> dict:
    target = Path(path)
    if not target.exists():
        return {"ok": False, "error": f"Папка не найдена: {path}"}
    try:
        if WIN:
            os.startfile(str(target))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


def open_ide(command: str, root: str) -> dict:
    command = (command or "").strip()
    if not command:
        return {"ok": False, "error": "Не задана команда открытия в IDE (open_with)"}
    expanded = expand(command, root)
    argv = split_command(expanded)
    if not argv:
        return {"ok": False, "error": f"Не удалось разобрать команду: {command}"}
    alias = IDE_ALIASES.get(argv[0].lower())
    if alias:
        found = shutil_which(alias)
        if found:
            argv[0] = found
        else:
            return {"ok": False, "error": f"Не найдено приложение: {alias}"}
    flags = 0
    if WIN:
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        flags = 0
    try:
        kwargs = {"cwd": root if Path(root).is_dir() else None, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if WIN:
            kwargs["creationflags"] = flags
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(argv, **kwargs)  # noqa: S603
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "command": argv}


def open_url(url: str) -> dict:
    url = (url or "").strip()
    if not url:
        return {"ok": False, "error": "Пустой адрес"}
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "http://" + url
    if not re.match(r"^https?://[^\s/]+", url, re.IGNORECASE):
        return {"ok": False, "error": f"Некорректный адрес: {url}"}
    try:
        webbrowser.open(url)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "url": url}


def shutil_which(name: str) -> str | None:
    import shutil

    if WIN:
        return shutil.which(name, pathext=".EXE;.CMD;.BAT")
    return shutil.which(name)


def check_port(host: str, port: int, timeout: float = 0.4) -> bool:
    import socket

    if not port:
        return False
    target = "127.0.0.1" if host in {"", "0.0.0.0", "::"} else host
    try:
        with socket.create_connection((target, int(port)), timeout=timeout):
            return True
    except OSError:
        return False
