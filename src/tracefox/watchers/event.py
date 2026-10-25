from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, Dict, List

from ..entry import EntryType, IncomingEntry
from ..utils import callable_name, to_jsonable
from .base import Watcher, safely

if TYPE_CHECKING:
    from ..core import Tracefox

FLASK_SIGNALS = {
    "template-rendered", "before-render-template", "request-started", "request-finished",
    "request-tearing-down", "got-request-exception", "appcontext-tearing-down", "appcontext-pushed",
    "appcontext-popped", "message-flashed",
}
_FRAMEWORK_PREFIXES = ("django.", "flask.", "blinker.", "celery.", "kombu.", "tracefox.")


class EventWatcher(Watcher):
    """Application events: Django signals, blinker signals (Flask) and ``tracefox.record_event``."""

    name = "events"

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._names: Dict[int, str] = {}

    def register(self) -> None:
        self.when_imported("django.dispatch.dispatcher", self._register_django)
        self.when_imported("blinker.base", self._register_blinker)

    @staticmethod
    def record_event(tracefox: "Tracefox", name: str, payload: Any, listeners: List[str], broadcast: bool = False) -> None:
        watcher = tracefox.watcher("events")
        if watcher is not None:
            watcher.record(name, payload, listeners, broadcast)

    def ignored(self, name: str) -> bool:
        if name in self.options.get("ignore", []):
            return True
        if self.options.get("include_framework"):
            return False
        return name.startswith(_FRAMEWORK_PREFIXES) or name in FLASK_SIGNALS

    @safely
    def record(self, name: str, payload: Any, listeners: List[str], broadcast: bool = False) -> None:
        if not self.active or self.ignored(name):
            return
        self.tracefox.record(IncomingEntry(EntryType.EVENT, {
            "name": name,
            "payload": to_jsonable(payload, depth=4),
            "listeners": [{"name": n, "queued": False} for n in listeners],
            "broadcast": broadcast,
        }))

    def _signal_name(self, signal: Any) -> str:
        key = id(signal)
        if key in self._names:
            return self._names[key]
        name = getattr(signal, "name", None) if isinstance(getattr(signal, "name", None), str) else None
        if name is None:
            candidates = []
            for module_name, module in list(sys.modules.items()):
                try:
                    candidates.extend(f"{module_name}.{attr}" for attr, value in vars(module).items() if value is signal)
                except Exception:
                    continue
            # A signal is usually imported elsewhere; prefer the module that looks like its home.
            candidates.sort(key=lambda c: ("signal" not in c.rsplit(".", 1)[0], not c.startswith(_FRAMEWORK_PREFIXES), len(c)))
            name = candidates[0] if candidates else None
        self._names[key] = name or repr(signal)
        return self._names[key]

    def _register_django(self, dispatcher: Any) -> None:
        watcher = self

        def receivers(signal, sender) -> List[str]:
            names = []
            try:
                for receiver in signal._live_receivers(sender):
                    if isinstance(receiver, (list, tuple)):  # Django 5 returns (sync, async) lists
                        names.extend(callable_name(r) for r in receiver)
                    else:
                        names.append(callable_name(receiver))
            except Exception:
                pass
            return names

        def make(original):
            def send(self, sender, **named):
                if watcher.active:
                    name = watcher._signal_name(self)
                    if not watcher.ignored(name):
                        watcher.record(name, {"sender": sender, **named}, receivers(self, sender))
                return original(self, sender, **named)
            return send

        def make_async(original):
            async def asend(self, sender, **named):
                if watcher.active:
                    name = watcher._signal_name(self)
                    if not watcher.ignored(name):
                        watcher.record(name, {"sender": sender, **named}, receivers(self, sender))
                return await original(self, sender, **named)
            return asend

        self.patch(dispatcher.Signal, "send", make)
        self.patch(dispatcher.Signal, "send_robust", make)
        if hasattr(dispatcher.Signal, "asend"):
            self.patch(dispatcher.Signal, "asend", make_async)
            self.patch(dispatcher.Signal, "asend_robust", make_async)

    def _register_blinker(self, base: Any) -> None:
        watcher = self

        def make(original):
            def send(self, *sender, **kwargs):
                if watcher.active:
                    name = watcher._signal_name(self)
                    if not watcher.ignored(name):
                        listeners = []
                        try:
                            listeners = [callable_name(r) for r in self.receivers_for(sender[0] if sender else None)]
                        except Exception:
                            pass
                        watcher.record(name, {"sender": sender[0] if sender else None, **kwargs}, listeners)
                return original(self, *sender, **kwargs)
            return send

        self.patch(base.Signal, "send", make)
