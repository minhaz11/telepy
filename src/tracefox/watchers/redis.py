from __future__ import annotations

import time
from typing import Any, Iterable

from ..entry import EntryType, IncomingEntry
from .base import Watcher, safely


def format_command(args: Iterable[Any]) -> str:
    parts = []
    for arg in args:
        if isinstance(arg, bytes):
            try:
                arg = arg.decode("utf-8")
            except UnicodeDecodeError:
                arg = "<binary>"
        text = str(arg)
        if len(text) > 200:
            text = text[:200] + "…"
        parts.append(f'"{text}"' if " " in text else text)
    return " ".join(parts)


def connection_name(client: Any) -> str:
    try:
        kwargs = client.connection_pool.connection_kwargs
        if "path" in kwargs:
            return f"unix:{kwargs['path']}/{kwargs.get('db', 0)}"
        return f"{kwargs.get('host', 'localhost')}:{kwargs.get('port', 6379)}/{kwargs.get('db', 0)}"
    except Exception:
        return "default"


class RedisWatcher(Watcher):
    name = "redis"

    def register(self) -> None:
        self.when_imported("redis", self._register)

    @safely
    def record_command(self, connection: str, command: str, duration_ms: float) -> None:
        if not self.active:
            return
        self.tracefox.record(IncomingEntry(EntryType.REDIS, {
            "connection": connection,
            "command": command,
            "time": f"{duration_ms:.2f}",
        }))

    def _register(self, redis: Any) -> None:
        watcher = self

        def make_sync(original):
            def execute_command(self, *args, **options):
                started = time.perf_counter()
                try:
                    return original(self, *args, **options)
                finally:
                    watcher.record_command(connection_name(self), format_command(args), (time.perf_counter() - started) * 1000)
            return execute_command

        def make_async(original):
            async def execute_command(self, *args, **options):
                started = time.perf_counter()
                try:
                    return await original(self, *args, **options)
                finally:
                    watcher.record_command(connection_name(self), format_command(args), (time.perf_counter() - started) * 1000)
            return execute_command

        def pipeline_command(pipe) -> str:
            commands = []
            for item in getattr(pipe, "command_stack", []):
                args = item.args if hasattr(item, "args") else item[0]
                commands.append(format_command(args))
            return "PIPELINE " + "; ".join(commands)

        def make_pipeline_sync(original):
            def execute(self, *args, **kwargs):
                command = pipeline_command(self)
                started = time.perf_counter()
                try:
                    return original(self, *args, **kwargs)
                finally:
                    watcher.record_command(connection_name(self), command, (time.perf_counter() - started) * 1000)
            return execute

        def make_pipeline_async(original):
            async def execute(self, *args, **kwargs):
                command = pipeline_command(self)
                started = time.perf_counter()
                try:
                    return await original(self, *args, **kwargs)
                finally:
                    watcher.record_command(connection_name(self), command, (time.perf_counter() - started) * 1000)
            return execute

        from redis import client as sync_client

        self.patch(sync_client.Redis, "execute_command", make_sync)
        self.patch(sync_client.Pipeline, "execute", make_pipeline_sync)
        try:
            from redis.asyncio import client as async_client
        except Exception:
            return
        self.patch(async_client.Redis, "execute_command", make_async)
        self.patch(async_client.Pipeline, "execute", make_pipeline_async)
