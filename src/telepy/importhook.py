"""Run callbacks when a module is imported, so watchers never import libraries the app doesn't use."""

from __future__ import annotations

import importlib.abc
import importlib.util
import logging
import sys
import threading
from typing import Any, Callable, Dict, List, Set

logger = logging.getLogger("telepy")

_callbacks: Dict[str, List[Callable[[Any], None]]] = {}
_resolving: Set[str] = set()
_lock = threading.RLock()


def when_imported(module_name: str, callback: Callable[[Any], None]) -> None:
    with _lock:
        module = sys.modules.get(module_name)
        if module is not None and getattr(module, "__spec__", None) is not None and not _initializing(module):
            _run(callback, module)
            return
        _callbacks.setdefault(module_name, []).append(callback)
        if not any(isinstance(f, _Finder) for f in sys.meta_path):
            sys.meta_path.insert(0, _Finder())


def _initializing(module: Any) -> bool:
    spec = getattr(module, "__spec__", None)
    return bool(getattr(spec, "_initializing", False))


def _run(callback: Callable[[Any], None], module: Any) -> None:
    try:
        callback(module)
    except Exception:
        logger.exception("Telepy import hook failed for %s", getattr(module, "__name__", module))


def _fire(name: str, module: Any) -> None:
    with _lock:
        callbacks = _callbacks.pop(name, [])
    for callback in callbacks:
        _run(callback, module)


class _LoaderProxy(importlib.abc.Loader):
    def __init__(self, name: str, loader: Any):
        self._name = name
        self._loader = loader

    def __getattr__(self, item: str) -> Any:
        return getattr(self._loader, item)

    def create_module(self, spec):
        return self._loader.create_module(spec) if hasattr(self._loader, "create_module") else None

    def exec_module(self, module):
        module.__loader__ = self._loader
        self._loader.exec_module(module)
        _fire(self._name, module)


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname not in _callbacks or fullname in _resolving:
            return None
        _resolving.add(fullname)
        try:
            spec = importlib.util.find_spec(fullname)
        except Exception:
            return None
        finally:
            _resolving.discard(fullname)
        if spec is None or spec.loader is None or not hasattr(spec.loader, "exec_module"):
            return None
        spec.loader = _LoaderProxy(fullname, spec.loader)
        return spec
