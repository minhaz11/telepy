from __future__ import annotations

import functools
import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Tuple

if TYPE_CHECKING:
    from ..core import Tracefox

logger = logging.getLogger("tracefox")

_MISSING = object()


class Watcher:
    name = ""

    def __init__(self, tracefox: "Tracefox", options: Dict[str, Any]):
        self.tracefox = tracefox
        self.options = options
        self._patches: List[Tuple[Any, str, Any]] = []
        self._unhooks: List[Callable[[], None]] = []
        self._stopped = False

    def register(self) -> None:
        pass

    def when_imported(self, module: str, callback: Callable[[Any], None]) -> None:
        """Hook a library once it is imported — unless this watcher was unregistered meanwhile."""
        from ..importhook import when_imported

        def run(mod: Any) -> None:
            if not self._stopped:
                callback(mod)

        when_imported(module, run)

    def unregister(self) -> None:
        self._stopped = True
        for target, attr, original in reversed(self._patches):
            if original is _MISSING:
                try:
                    delattr(target, attr)
                except AttributeError:
                    pass
            else:
                setattr(target, attr, original)
        self._patches.clear()
        for unhook in reversed(self._unhooks):
            try:
                unhook()
            except Exception:
                pass
        self._unhooks.clear()

    @property
    def active(self) -> bool:
        return self.tracefox.watchers.get(self.name) is self and self.tracefox.should_record()

    def patch(self, target: Any, attr: str, make: Callable[[Callable], Callable]) -> None:
        """Replace ``target.attr`` with ``make(original)``, remembering how to undo it."""
        original = target.__dict__.get(attr, _MISSING) if isinstance(target, type) else getattr(target, attr, _MISSING)
        current = getattr(target, attr, None)
        if current is None or getattr(current, "__tracefox_patched__", False):
            return
        wrapper = make(current)
        if callable(current) and not isinstance(current, type) and isinstance(wrapper, type(lambda: None)):
            functools.update_wrapper(wrapper, current)
        try:
            wrapper.__tracefox_patched__ = True
        except (AttributeError, TypeError):
            getattr(wrapper, "__func__", wrapper).__tracefox_patched__ = True
        setattr(target, attr, wrapper)
        self._patches.append((target, attr, original))

    def on_unregister(self, callback: Callable[[], None]) -> None:
        self._unhooks.append(callback)


def safely(fn: Callable) -> Callable:
    """Never let a tracefox failure break the host application."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            logger.debug("Tracefox watcher error in %s", fn.__qualname__, exc_info=True)
            return None

    return wrapper


def try_import(name: str) -> Any:
    try:
        return __import__(name, fromlist=["_"])
    except Exception:
        return None
