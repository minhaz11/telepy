from __future__ import annotations

from typing import Any, Iterable, Optional

from ..entry import EntryType, IncomingEntry
from ..utils import relative_path
from .base import Watcher, safely

_DJANGO_BUILTINS = {"True", "False", "None", "csrf_token", "request", "user", "perms", "messages",
                    "DEFAULT_MESSAGE_LEVELS", "debug", "sql_queries", "view"}


class ViewWatcher(Watcher):
    """Rendered templates: Jinja2 (Flask, FastAPI/Starlette templates) and Django templates."""

    name = "views"

    def register(self) -> None:
        self.when_imported("jinja2.environment", self._register_jinja)
        self.when_imported("django.template.base", self._register_django)

    @safely
    def record_view(self, name: Optional[str], path: Optional[str], data_keys: Iterable[str]) -> None:
        if not self.active:
            return
        self.telepy.record(IncomingEntry(EntryType.VIEW, {
            "name": name or "(string template)",
            "path": relative_path(path),
            "data": sorted({str(k) for k in data_keys}),
            "composers": [],
        }))

    def _register_jinja(self, environment: Any) -> None:
        watcher = self

        def keys(args, kwargs):
            data = dict(*args, **kwargs) if args or kwargs else {}
            return [k for k in data if not k.startswith("_")]

        def make(original):
            def render(self, *args, **kwargs):
                watcher.record_view(self.name, self.filename, keys(args, kwargs))
                return original(self, *args, **kwargs)
            return render

        def make_async(original):
            async def render_async(self, *args, **kwargs):
                watcher.record_view(self.name, self.filename, keys(args, kwargs))
                return await original(self, *args, **kwargs)
            return render_async

        self.patch(environment.Template, "render", make)
        self.patch(environment.Template, "render_async", make_async)

    def _register_django(self, base: Any) -> None:
        watcher = self

        def make(original):
            def render(self, context):
                origin = getattr(self, "origin", None)
                name = getattr(origin, "template_name", None) or getattr(self, "name", None)
                path = getattr(origin, "name", None)
                if path == "<unknown source>":
                    path = None
                try:
                    data_keys = [k for k in context.flatten() if k not in _DJANGO_BUILTINS]
                except Exception:
                    data_keys = []
                watcher.record_view(name, path, data_keys)
                return original(self, context)
            return render

        self.patch(base.Template, "render", make)
