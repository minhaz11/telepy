from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .entry import EntryUpdate, IncomingEntry, ordered_uuid


@dataclass
class Batch:
    id: str = field(default_factory=ordered_uuid)
    entries: List[IncomingEntry] = field(default_factory=list)
    updates: List[EntryUpdate] = field(default_factory=list)
    # Scratch space for watchers that aggregate per batch (e.g. model hydrations).
    state: Dict[str, Any] = field(default_factory=dict)
    finalizers: List[Callable[["Batch"], None]] = field(default_factory=list)
    user: Optional[Dict[str, Any]] = None


current_batch: ContextVar[Optional[Batch]] = ContextVar("tracefox_batch", default=None)
suspended: ContextVar[bool] = ContextVar("tracefox_suspended", default=False)
