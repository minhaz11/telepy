from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from ..entry import EntryType, EntryUpdate, IncomingEntry, ordered_uuid
from ..utils import to_jsonable
from .base import Watcher, safely
from .job import BATCH_HEADER


class BatchWatcher(Watcher):
    """Groups of jobs dispatched together (Celery ``group`` / ``chord``) and their progress."""

    name = "batches"

    def register(self) -> None:
        self.when_imported("celery", self._register_celery)

    @safely
    def record_batch(self, name: Optional[str], total: int, batch_id: Optional[str] = None,
                     options: Any = None, uuid: Optional[str] = None) -> Optional[str]:
        if not self.active:
            return None
        entry = IncomingEntry(EntryType.BATCH, {
            "id": batch_id,
            "name": name,
            "totalJobs": total,
            "pendingJobs": total,
            "processedJobs": 0,
            "failedJobs": 0,
            "options": to_jsonable(options or {}, depth=3),
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }, uuid=uuid or ordered_uuid())
        self.telepy.record(entry)
        return entry.uuid

    def record_job_finished(self, uuid: str, failed: bool = False) -> None:
        increments = {"pendingJobs": -1, ("failedJobs" if failed else "processedJobs"): 1}
        self.telepy.record_update(EntryUpdate(uuid, EntryType.BATCH, increments=increments))

    def _register_celery(self, _celery: Any) -> None:
        from celery import canvas

        watcher = self

        def make(original):
            def apply_async(self, args=None, kwargs=None, *a, **options):
                if not watcher.active or options.get("_telepy_skip"):
                    return original(self, args, kwargs, *a, **options)
                tasks = list(getattr(self, "tasks", []) or [])
                uuid = ordered_uuid()
                names = sorted({getattr(t, "task", str(t)) for t in tasks})
                for task in tasks:
                    try:
                        headers = dict(task.options.get("headers") or {})
                        headers[BATCH_HEADER] = uuid
                        task.set(headers=headers)
                    except Exception:
                        pass
                watcher.record_batch(
                    name=", ".join(names) if names else "group", total=len(tasks),
                    batch_id=options.get("group_id"), options={"tasks": names}, uuid=uuid,
                )
                return original(self, args, kwargs, *a, **options)
            return apply_async

        self.patch(canvas.group, "apply_async", make)
