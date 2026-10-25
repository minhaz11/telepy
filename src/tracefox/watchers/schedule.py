from __future__ import annotations

from typing import Any, Optional

from ..entry import EntryType, IncomingEntry
from ..utils import callable_name, to_jsonable
from .base import Watcher, safely


class ScheduleWatcher(Watcher):
    """Scheduled tasks run by APScheduler (3.x) or dispatched by Celery beat."""

    name = "schedule"

    def register(self) -> None:
        self.when_imported("apscheduler.executors.base", self._register_apscheduler)
        self.when_imported("apscheduler.executors.pool", self._register_apscheduler_pool)
        self.when_imported("celery.beat", self._register_celery_beat)

    @safely
    def record_task(self, command: str, expression: Optional[str] = None, description: Optional[str] = None,
                    timezone: Optional[str] = None, output: Any = None, user: Optional[str] = None) -> None:
        if not self.active:
            return
        entry = IncomingEntry(EntryType.SCHEDULED_TASK, {
            "command": command,
            "description": description,
            "expression": expression,
            "timezone": timezone,
            "user": user,
            "output": output if isinstance(output, str) else to_jsonable(output, depth=4),
        })
        self.tracefox.record(entry)

    def _job_info(self, job: Any):
        trigger = getattr(job, "trigger", None)
        tz = getattr(trigger, "timezone", None)
        return (
            getattr(job, "name", None) or callable_name(getattr(job, "func", job)),
            str(trigger) if trigger is not None else None,
            getattr(job, "id", None),
            str(tz) if tz is not None else None,
        )

    def _output(self, events: Any) -> str:
        lines = []
        for event in events or []:
            if getattr(event, "exception", None) is not None:
                lines.append(f"{type(event.exception).__name__}: {event.exception}")
            elif getattr(event, "retval", None) is not None:
                lines.append(repr(event.retval))
        return "\n".join(lines)

    def _make_run_job(self, original):
        watcher = self

        def run_job(job, *args, **kwargs):
            if not watcher.active:
                return original(job, *args, **kwargs)
            command, expression, description, tz = watcher._job_info(job)
            with watcher.tracefox.batch():
                events = original(job, *args, **kwargs)
                watcher.record_task(command, expression, description, tz, watcher._output(events))
            return events
        return run_job

    def _make_run_coroutine_job(self, original):
        watcher = self

        async def run_coroutine_job(job, *args, **kwargs):
            if not watcher.active:
                return await original(job, *args, **kwargs)
            command, expression, description, tz = watcher._job_info(job)
            with watcher.tracefox.batch():
                events = await original(job, *args, **kwargs)
                watcher.record_task(command, expression, description, tz, watcher._output(events))
            return events
        return run_coroutine_job

    def _register_apscheduler(self, base: Any) -> None:
        if hasattr(base, "run_job"):
            self.patch(base, "run_job", self._make_run_job)
        if hasattr(base, "run_coroutine_job"):
            self.patch(base, "run_coroutine_job", self._make_run_coroutine_job)

    def _register_apscheduler_pool(self, pool: Any) -> None:
        if hasattr(pool, "run_job"):
            self.patch(pool, "run_job", self._make_run_job)

    def _register_celery_beat(self, beat: Any) -> None:
        watcher = self

        def make(original):
            def apply_entry(self, entry, producer=None):
                result = original(self, entry, producer=producer)
                tz = getattr(getattr(self, "app", None), "timezone", None)
                watcher.record_task(
                    entry.task, str(entry.schedule), entry.name, str(tz) if tz else None, "Dispatched to queue",
                )
                return result
            return apply_entry

        self.patch(beat.Scheduler, "apply_entry", make)
