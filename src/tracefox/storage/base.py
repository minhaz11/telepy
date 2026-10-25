from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

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


class Storage(ABC):
    """Persistence for entries, tags, monitored tags and recording state."""

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
