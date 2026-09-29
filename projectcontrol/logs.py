from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class LogEntry:
    seq: int
    ts: float
    target_id: str
    target_name: str
    stream: str
    text: str

    def as_dict(self) -> dict:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "target_id": self.target_id,
            "target_name": self.target_name,
            "stream": self.stream,
            "text": self.text,
        }


class RingLog:
    """Thread safe ring buffer: keeps only the last `maxlen` entries."""

    def __init__(self, maxlen: int = 2000):
        self._lock = threading.Lock()
        self._seq = 0
        self._maxlen = max(1, int(maxlen))
        self._items: deque[LogEntry] = deque(maxlen=self._maxlen)

    @property
    def maxlen(self) -> int:
        return self._maxlen

    def set_maxlen(self, maxlen: int) -> None:
        maxlen = max(1, int(maxlen))
        with self._lock:
            if maxlen == self._maxlen:
                return
            kept = list(self._items)[-maxlen:]
            self._maxlen = maxlen
            self._items = deque(kept, maxlen=maxlen)

    def append(self, text: str, target_id: str = "", target_name: str = "", stream: str = "stdout") -> LogEntry:
        with self._lock:
            self._seq += 1
            entry = LogEntry(self._seq, time.time(), target_id, target_name, stream, text)
            self._items.append(entry)
            return entry

    def append_many(self, texts: Iterable[str], target_id: str = "", target_name: str = "", stream: str = "stdout") -> list[LogEntry]:
        return [self.append(t, target_id, target_name, stream) for t in texts]

    def tail(self, since: int = 0, limit: int = 1000) -> tuple[list[LogEntry], int]:
        """Return (entries newer than `since`, total dropped count)."""
        with self._lock:
            dropped = max(0, self._seq - len(self._items))
            items = [e for e in self._items if e.seq > since]
        if limit and len(items) > limit:
            items = items[-limit:]
        return items, dropped

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def stats(self) -> dict:
        with self._lock:
            return {
                "kept": len(self._items),
                "maxlen": self._maxlen,
                "total": self._seq,
                "dropped": max(0, self._seq - len(self._items)),
            }


class LineSplitter:
    """Splits a byte/str stream chunk into lines, flushing the tail on close."""

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: str) -> list[str]:
        self._buffer += chunk
        lines: list[str] = []
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            lines.append(line.rstrip("\r"))
        return lines

    def flush(self) -> list[str]:
        rest, self._buffer = self._buffer, ""
        rest = rest.rstrip("\r")
        return [rest] if rest else []
