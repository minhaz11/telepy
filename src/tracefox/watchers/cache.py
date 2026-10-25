from __future__ import annotations

import fnmatch
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from ..entry import EntryType, IncomingEntry
from ..utils import to_jsonable
from .base import Watcher, safely

if TYPE_CHECKING:
    from ..core import Tracefox

_inside: ContextVar[bool] = ContextVar("tracefox_cache_inside", default=False)
_MISS = object()


class CacheWatcher(Watcher):
    """Cache hits, misses, writes and deletes (Django cache framework, Flask-Caching, manual)."""

    name = "cache"

    def register(self) -> None:
        self.when_imported("django.core.cache", self._register_django)
        self.when_imported("flask_caching", self._register_flask_caching)

    @staticmethod
    def record_cache(tracefox: "Tracefox", action: str, key: Any, value: Any = None, expiration: Any = None) -> None:
        watcher = tracefox.watcher("cache")
        if watcher is not None:
            watcher.record(action, key, value, expiration)

    @safely
    def record(self, action: str, key: Any, value: Any = None, expiration: Any = None) -> None:
        if not self.active:
            return
        key = str(key)
        hidden = any(fnmatch.fnmatchcase(key, p) for p in self.options.get("hidden", []))
        content = {"type": action, "key": key}
        if action in ("hit", "set"):
            content["value"] = "********" if hidden else to_jsonable(value, depth=4)
        if action == "set":
            content["expiration"] = expiration
        self.tracefox.record(IncomingEntry(EntryType.CACHE, content))

    def _wrap(self, cls: Any, supports_default: bool) -> None:
        watcher = self

        def guarded(fn):
            def inner(*args, **kwargs):
                if _inside.get():
                    return fn(*args, _record=False, **kwargs)
                token = _inside.set(True)
                try:
                    return fn(*args, _record=True, **kwargs)
                finally:
                    _inside.reset(token)
            return inner

        def make_get(original):
            @guarded
            def get(self, key, default=None, *args, _record, **kwargs):
                if supports_default:
                    value = original(self, key, _MISS, *args, **kwargs)
                    missed = value is _MISS
                else:
                    value = original(self, key, *args, **kwargs)
                    missed = value is None
                if _record:
                    watcher.record("missed" if missed else "hit", key, value)
                return default if missed else value
            return get

        def make_set(original):
            @guarded
            def set_(self, key, value, *args, _record, **kwargs):
                result = original(self, key, value, *args, **kwargs)
                if _record:
                    timeout = args[0] if args else kwargs.get("timeout")
                    watcher.record("set", key, value, timeout if isinstance(timeout, (int, float)) else None)
                return result
            return set_

        def make_delete(original):
            @guarded
            def delete(self, key, *args, _record, **kwargs):
                result = original(self, key, *args, **kwargs)
                if _record:
                    watcher.record("forget", key)
                return result
            return delete

        def make_get_many(original):
            @guarded
            def get_many(self, keys, *args, _record, **kwargs):
                keys = list(keys)
                result = original(self, keys, *args, **kwargs)
                if _record:
                    if isinstance(result, dict):
                        for key in keys:
                            watcher.record("hit" if key in result else "missed", key, result.get(key))
                    else:
                        for key, value in zip(keys, result):
                            watcher.record("missed" if value is None else "hit", key, value)
                return result
            return get_many

        def make_set_many(original):
            @guarded
            def set_many(self, mapping, *args, _record, **kwargs):
                result = original(self, mapping, *args, **kwargs)
                if _record:
                    timeout = args[0] if args else kwargs.get("timeout")
                    for key, value in dict(mapping).items():
                        watcher.record("set", key, value, timeout if isinstance(timeout, (int, float)) else None)
                return result
            return set_many

        def make_delete_many(original):
            @guarded
            def delete_many(self, keys, *args, _record, **kwargs):
                keys = list(keys)
                result = original(self, keys, *args, **kwargs)
                if _record:
                    for key in keys:
                        watcher.record("forget", key)
                return result
            return delete_many

        for attr, make in (
            ("get", make_get), ("set", make_set), ("add", make_set), ("delete", make_delete),
            ("get_many", make_get_many), ("set_many", make_set_many), ("delete_many", make_delete_many),
        ):
            if hasattr(cls, attr):
                self.patch(cls, attr, make)

    def _register_django(self, _module: Any) -> None:
        from django.conf import settings
        from django.utils.module_loading import import_string

        if not settings.configured:
            return
        seen = set()
        for config in getattr(settings, "CACHES", {}).values():
            try:
                cls = import_string(config.get("BACKEND", ""))
            except Exception:
                continue
            for klass in cls.__mro__:
                if klass in seen or klass is object:
                    continue
                if any(name in klass.__dict__ for name in ("get", "set", "delete")):
                    seen.add(klass)
                    self._wrap(klass, supports_default=True)

    def _register_flask_caching(self, flask_caching: Any) -> None:
        self._wrap(flask_caching.Cache, supports_default=False)
