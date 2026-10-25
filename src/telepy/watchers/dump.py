from __future__ import annotations

import pprint
from typing import TYPE_CHECKING, Any, Sequence

from ..entry import EntryType, IncomingEntry
from ..utils import find_app_frame, relative_path, to_jsonable
from .base import Watcher

if TYPE_CHECKING:
    from ..core import Telepy


class DumpWatcher(Watcher):
    """``telepy.dump(value)`` — like Laravel's ``dump()``, rendered on the Dumps screen."""

    name = "dumps"


def record_dump(telepy: "Telepy", values: Sequence[Any]) -> None:
    watcher = telepy.watcher("dumps")
    if watcher is None or not watcher.active:
        return
    file, line = find_app_frame(2)
    for value in values:
        telepy.record(IncomingEntry(EntryType.DUMP, {
            "dump": pprint.pformat(value, width=100, sort_dicts=False),
            "value": to_jsonable(value, depth=8),
            "type": type(value).__name__,
            "file": relative_path(file),
            "line": line,
        }))
