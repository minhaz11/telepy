from __future__ import annotations

import functools
import inspect
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from ..entry import EntryType, EntryUpdate, IncomingEntry, ordered_uuid
from ..utils import callable_name, format_exception, to_jsonable
from .base import Watcher, safely

if TYPE_CHECKING:
    from ..core import Tracefox

HEADER = "tracefox_uuid"
BATCH_HEADER = "tracefox_batch_uuid"


def _exception_content(exc: BaseException) -> Dict[str, Any]:
    data = format_exception(exc)
    return {"message": f"{data['class']}: {data['message']}", "trace": data["trace"], "line_preview": data["line_preview"],
            "file": data["file"], "line": data["line"]}


class JobWatcher(Watcher):
    """Celery tasks (dispatch → processed / failed), Starlette background tasks and ``@tracefox.job``."""

    name = "jobs"

    def register(self) -> None:
        self.when_imported("celery", self._register_celery)
        self.when_imported("starlette.background", self._register_starlette)

    @safely
    def record_dispatch(self, *, name: str, connection: str, queue: str, data: Any, tries: Any = None,
                        timeout: Any = None, uuid: Optional[str] = None, batch_uuid: Optional[str] = None) -> Optional[str]:
        if not self.active:
            return None
        entry = IncomingEntry(EntryType.JOB, {
            "status": "pending",
            "connection": connection,
            "queue": queue,
            "name": name,
            "tries": tries,
            "timeout": timeout,
            "data": to_jsonable(data, depth=6),
        }, uuid=uuid or ordered_uuid())
        if batch_uuid:
            entry.content["batch_id"] = batch_uuid
            entry.add_tags([f"Batch:{batch_uuid}"])
        self.tracefox.record(entry)
        return entry.uuid

    def record_processed(self, uuid: str, processing_batch_id: Optional[str]) -> None:
        self.tracefox.record_update(EntryUpdate(uuid, EntryType.JOB, {"status": "processed", "processing_batch_id": processing_batch_id}))

    def record_failed(self, uuid: str, exc: BaseException, processing_batch_id: Optional[str]) -> None:
        self.tracefox.record_update(EntryUpdate(
            uuid, EntryType.JOB,
            {"status": "failed", "exception": _exception_content(exc), "processing_batch_id": processing_batch_id},
            tags_added=["failed"],
        ))

    def run_job(self, fn: Callable, args: tuple, kwargs: dict, *, name: str, queue: str, connection: str):
        """Record a job that is dispatched and processed in one go (sync / in-process jobs)."""
        uuid = self.record_dispatch(name=name, connection=connection, queue=queue, data={"args": args, "kwargs": kwargs})
        with self.tracefox.batch() as batch:
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                if uuid:
                    self.record_failed(uuid, exc, batch.id)
                raise
            if uuid:
                self.record_processed(uuid, batch.id)
            return result

    async def run_job_async(self, fn: Callable, args: tuple, kwargs: dict, *, name: str, queue: str, connection: str):
        uuid = self.record_dispatch(name=name, connection=connection, queue=queue, data={"args": args, "kwargs": kwargs})
        with self.tracefox.batch() as batch:
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                if uuid:
                    self.record_failed(uuid, exc, batch.id)
                raise
            if uuid:
                self.record_processed(uuid, batch.id)
            return result

    # -- Celery ----------------------------------------------------------------------

    def _register_celery(self, _celery: Any) -> None:
        from celery import signals

        watcher = self
        running: Dict[str, Any] = {}

        def before_publish(sender=None, body=None, exchange=None, routing_key=None, headers=None, properties=None, **_):
            if headers is None or not watcher.active:
                return
            args, kwargs = (body[0], body[1]) if isinstance(body, (list, tuple)) and len(body) >= 2 else (None, None)
            uuid = watcher.record_dispatch(
                name=headers.get("task", sender),
                connection="celery",
                queue=routing_key or (properties or {}).get("delivery_info", {}).get("routing_key") or "celery",
                data={"args": args, "kwargs": kwargs, "id": headers.get("id")},
                tries=headers.get("retries"),
                timeout=(headers.get("timelimit") or [None, None])[1],
                batch_uuid=headers.get(BATCH_HEADER),
            )
            if uuid:
                headers[HEADER] = uuid

        def header(task, key):
            request = getattr(task, "request", None)
            value = getattr(request, key, None)
            if value is None:
                value = (getattr(request, "headers", None) or {}).get(key)
            return value

        def prerun(task_id=None, task=None, **_):
            if task is None:
                return
            batch, token = watcher.tracefox.start_batch()
            running[task_id] = (batch, token, header(task, HEADER), header(task, BATCH_HEADER))

        def finish(task_id, exc=None):
            state = running.pop(task_id, None)
            if state is None:
                return
            batch, token, uuid, batch_uuid = state
            if uuid:
                if exc is None:
                    watcher.record_processed(uuid, batch.id)
                else:
                    watcher.record_failed(uuid, exc, batch.id)
            if batch_uuid:
                batch_watcher = watcher.tracefox.watcher("batches")
                if batch_watcher is not None:
                    batch_watcher.record_job_finished(batch_uuid, failed=exc is not None)
            watcher.tracefox.end_batch(batch, token)

        failures: Dict[str, BaseException] = {}

        def on_failure(task_id=None, exception=None, **_):
            exceptions = watcher.tracefox.watcher("exceptions")
            if exceptions is not None and exception is not None:
                exceptions.record_exception(exception)
            failures[task_id] = exception

        def postrun(task_id=None, **_):
            finish(task_id, failures.pop(task_id, None))

        signals.before_task_publish.connect(before_publish, weak=False)
        signals.task_prerun.connect(prerun, weak=False)
        signals.task_failure.connect(on_failure, weak=False)
        signals.task_postrun.connect(postrun, weak=False)

        def unhook():
            signals.before_task_publish.disconnect(before_publish)
            signals.task_prerun.disconnect(prerun)
            signals.task_failure.disconnect(on_failure)
            signals.task_postrun.disconnect(postrun)

        self.on_unregister(unhook)

    # -- Starlette / FastAPI background tasks ------------------------------------------

    def _register_starlette(self, module: Any) -> None:
        watcher = self

        def make(original):
            async def __call__(self):
                if not watcher.active:
                    return await original(self)
                name = callable_name(self.func)

                async def run():
                    return await original(self)

                return await watcher.run_job_async(run, (), {}, name=name, queue="background", connection="background_tasks")
            return __call__

        self.patch(module.BackgroundTask, "__call__", make)


def job_decorator(tracefox: "Tracefox", name: Optional[str], queue: str, connection: str) -> Callable:
    def decorator(fn: Callable) -> Callable:
        job_name = name or callable_name(fn)

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                watcher = tracefox.watcher("jobs")
                if watcher is None or not watcher.active:
                    return await fn(*args, **kwargs)
                return await watcher.run_job_async(fn, args, kwargs, name=job_name, queue=queue, connection=connection)
            return async_wrapper

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            watcher = tracefox.watcher("jobs")
            if watcher is None or not watcher.active:
                return fn(*args, **kwargs)
            return watcher.run_job(fn, args, kwargs, name=job_name, queue=queue, connection=connection)
        return wrapper

    return decorator
