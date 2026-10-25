from __future__ import annotations

import functools
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from ..context import suspended
from ..entry import EntryUpdate, IncomingEntry


@dataclass
class EntryQueryOptions:
    before_sequence: Optional[int] = None
    after_sequence: Optional[int] = None
    tag: Optional[str] = None
    family_hash: Optional[str] = None
    batch_id: Optional[str] = None
    uuids: Optional[List[str]] = None
    limit: int = 50


def _quiet(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        token = suspended.set(True)
        try:
            return fn(*args, **kwargs)
        finally:
            suspended.reset(token)
    return wrapper


class Storage(ABC):
    """Persistence for entries, tags, monitored tags and recording state.

    Every public method runs with recording suspended, so storage queries on a shared
    (application) database never show up as entries themselves.
    """

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for name, value in list(vars(cls).items()):
            if callable(value) and not name.startswith("_") and not isinstance(value, (staticmethod, classmethod, type)):
                setattr(cls, name, _quiet(value))

    @abstractmethod
    def migrate(self) -> None: ...

    @abstractmethod
    def store(self, entries: List[IncomingEntry]) -> None: ...

    @abstractmethod
    def update(self, updates: List[EntryUpdate]) -> List[EntryUpdate]: ...

    @abstractmethod
    def get(self, type: Optional[str], options: EntryQueryOptions) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def find(self, uuid: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    def batch_entries(self, batch_ids: Iterable[str]) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def monitored_tags(self) -> List[str]: ...

    @abstractmethod
    def monitor(self, tags: Iterable[str]) -> None: ...

    @abstractmethod
    def stop_monitoring(self, tags: Iterable[str]) -> None: ...

    @abstractmethod
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]: ...

    @abstractmethod
    def set_setting(self, key: str, value: Optional[str]) -> None: ...

    @abstractmethod
    def prune(self, before: datetime, keep_exceptions: bool = False) -> int: ...

    @abstractmethod
    def clear(self) -> None: ...

    def counts(self) -> Dict[str, int]:
        return {}

    def reset_after_fork(self) -> None:
        pass
