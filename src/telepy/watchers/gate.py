from __future__ import annotations

import functools
import inspect
from typing import TYPE_CHECKING, Any, Callable, Optional

from ..entry import EntryType, IncomingEntry
from ..utils import callable_name, find_app_frame, relative_path, to_jsonable
from .base import Watcher, safely

if TYPE_CHECKING:
    from ..core import Telepy


class GateWatcher(Watcher):
    """Authorization checks: Django ``has_perm``, ``@telepy.gate`` and ``telepy.record_gate``."""

    name = "gates"

    def register(self) -> None:
        self.when_imported("django.contrib.auth.models", self._register_django)

    @staticmethod
    def record_gate(telepy: "Telepy", ability: str, result: Any, arguments: Any = None) -> None:
        watcher = telepy.watcher("gates")
        if watcher is not None:
            watcher.record(ability, result, arguments)

    @safely
    def record(self, ability: str, result: Any, arguments: Any = None) -> None:
        if not self.active or ability in self.options.get("ignore_abilities", []):
            return
        file, line = find_app_frame(2)
        self.telepy.record(IncomingEntry(EntryType.GATE, {
            "ability": ability,
            "result": "allowed" if result else "denied",
            "arguments": to_jsonable(arguments if arguments is not None else [], depth=3),
            "file": relative_path(file),
            "line": line,
        }))

    def _register_django(self, models: Any) -> None:
        watcher = self

        def make(original):
            def has_perm(self, perm, obj=None):
                result = original(self, perm, obj)
                watcher.record(perm, result, [obj] if obj is not None else [])
                return result
            return has_perm

        self.patch(models.PermissionsMixin, "has_perm", make)
        self.patch(models.AnonymousUser, "has_perm", make)


def gate_decorator(telepy: "Telepy", ability: Optional[str]) -> Callable:
    def decorator(fn: Callable) -> Callable:
        name = ability or callable_name(fn)

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                result = await fn(*args, **kwargs)
                GateWatcher.record_gate(telepy, name, result, list(args) + ([kwargs] if kwargs else []))
                return result
            return async_wrapper

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            result = fn(*args, **kwargs)
            GateWatcher.record_gate(telepy, name, result, list(args) + ([kwargs] if kwargs else []))
            return result
        return wrapper

    return decorator
