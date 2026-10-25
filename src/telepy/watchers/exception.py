from __future__ import annotations

import logging
import sys
import threading
from typing import Any, Dict, Optional

from ..entry import EntryType, IncomingEntry
from ..utils import class_name, exception_context, format_exception, md5, to_jsonable
from .base import Watcher, safely


class ExceptionWatcher(Watcher):
    """Unhandled exceptions (via integrations / excepthooks) and anything logged with ``exc_info``."""

    name = "exceptions"

    def register(self) -> None:
        previous_hook = sys.excepthook
        previous_thread_hook = threading.excepthook

        def excepthook(exc_type, exc, tb):
            self.record_exception(exc)
            self.telepy.flush(2)
            previous_hook(exc_type, exc, tb)

        def thread_excepthook(args):
            if args.exc_value is not None:
                self.record_exception(args.exc_value)
            previous_thread_hook(args)

        sys.excepthook = excepthook
        threading.excepthook = thread_excepthook

        watcher = self

        class _Handler(logging.Handler):
            def emit(self, record):
                if record.exc_info and record.exc_info[1] is not None and not record.name.startswith("telepy"):
                    watcher.record_exception(record.exc_info[1], {"log_message": record.getMessage(), "logger": record.name})

        handler = _Handler(logging.ERROR)
        logging.getLogger().addHandler(handler)

        def unhook():
            logging.getLogger().removeHandler(handler)
            if sys.excepthook is excepthook:
                sys.excepthook = previous_hook
            if threading.excepthook is thread_excepthook:
                threading.excepthook = previous_thread_hook

        self.on_unregister(unhook)

    def _ignored(self, exc: BaseException) -> bool:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
            return True
        ignored = self.options.get("ignore", [])
        return any(class_name(exc) == name or type(exc).__name__ == name for name in ignored)

    @safely
    def record_exception(self, exc: BaseException, context: Optional[Dict[str, Any]] = None) -> None:
        if exc is None or not self.active or self._ignored(exc):
            return
        if getattr(exc, "__telepy_recorded__", False):
            return
        try:
            exc.__telepy_recorded__ = True
        except Exception:
            pass
        data = format_exception(exc)
        data["context"] = {**exception_context(exc), **to_jsonable(context or {}, depth=4)} or None
        entry = IncomingEntry(EntryType.EXCEPTION, data, family_hash=md5(f"{data['class']}{data['file']}{data['line']}"))
        self.telepy.record(entry)
