from __future__ import annotations

import logging
from typing import Any, Dict

from ..entry import EntryType, IncomingEntry
from ..utils import relative_path, to_jsonable
from .base import Watcher

_STANDARD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime", "taskName"}
_LEVELS = {"WARN": "warning", "FATAL": "critical"}


class TracefoxLogHandler(logging.Handler):
    def __init__(self, watcher: "LogWatcher", level: int):
        super().__init__(level)
        self.watcher = watcher

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.watcher.handle_record(record)
        except Exception:
            pass


class LogWatcher(Watcher):
    """Records log records; records carrying an exception are routed to the exception watcher."""

    name = "logs"

    def register(self) -> None:
        level = self.options.get("level", "DEBUG")
        level = logging.getLevelName(level.upper()) if isinstance(level, str) else int(level)
        self.handler = TracefoxLogHandler(self, level)
        root = logging.getLogger()
        root.addHandler(self.handler)
        self.on_unregister(lambda: root.removeHandler(self.handler))
        if root.level > level:
            # The root logger would drop these before they reach our handler.
            previous = root.level
            quiet = [h for h in root.handlers if h is not self.handler and h.level == logging.NOTSET]
            for h in quiet:
                h.setLevel(previous)
            root.setLevel(level)

            def restore():
                root.setLevel(previous)
                for h in quiet:
                    h.setLevel(logging.NOTSET)

            self.on_unregister(restore)

    def _ignored(self, name: str) -> bool:
        for prefix in self.options.get("ignore_loggers", []):
            if name == prefix or name.startswith(prefix + "."):
                return True
        return False

    def handle_record(self, record: logging.LogRecord) -> None:
        if self._ignored(record.name) or not self.tracefox.should_record():
            return
        exc = record.exc_info[1] if record.exc_info else None
        exceptions = self.tracefox.watcher("exceptions")
        if exc is not None and exceptions is not None:
            exceptions.record_exception(exc, {"log_message": record.getMessage(), "logger": record.name})
            return
        if self.tracefox.watcher(self.name) is not self:
            return
        context: Dict[str, Any] = {
            key: to_jsonable(value, depth=4)
            for key, value in vars(record).items()
            if key not in _STANDARD_ATTRS and not key.startswith("_")
        }
        if exc is not None:
            context["exception"] = f"{type(exc).__name__}: {exc}"
        level = _LEVELS.get(record.levelname, record.levelname.lower())
        entry = IncomingEntry(EntryType.LOG, {
            "level": level,
            "message": record.getMessage(),
            "channel": record.name,
            "context": context,
            "location": f"{relative_path(record.pathname)}:{record.lineno}",
        })
        if level in ("error", "critical"):
            entry.add_tags([level])
        self.tracefox.record(entry)
