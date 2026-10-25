from __future__ import annotations

import datetime as _dt
import re
import time
from typing import Any, Optional, Sequence

from ..entry import EntryType, IncomingEntry
from ..utils import find_app_frame, md5, relative_path, to_jsonable
from .base import Watcher, safely

_NAMED = re.compile(r"%\((\w+)\)s|(?<![:\w]):(\w+)")
_POSITIONAL = re.compile(r"\?|%s|\$\d+|(?<![:\w]):\d+")


def quote(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "'<binary>'"
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return f"'{value.isoformat(sep=' ') if isinstance(value, _dt.datetime) else value.isoformat()}'"
    return "'" + str(value).replace("'", "''") + "'"


def interpolate(sql: str, params: Any) -> str:
    if not params:
        return sql
    try:
        if isinstance(params, dict):
            return _NAMED.sub(lambda m: quote(params.get(m.group(1) or m.group(2), m.group(0))) if (m.group(1) or m.group(2)) in params else m.group(0), sql)
        values = list(params)
        index = iter(range(len(values)))

        def replace(match):
            token = match.group(0)
            if token.startswith("$") or token.startswith(":"):
                pos = int(token[1:]) - 1
                return quote(values[pos]) if 0 <= pos < len(values) else token
            try:
                return quote(values[next(index)])
            except StopIteration:
                return token

        return _POSITIONAL.sub(replace, sql)
    except Exception:
        return sql


class QueryWatcher(Watcher):
    name = "queries"

    def register(self) -> None:
        self.when_imported("sqlalchemy", self._register_sqlalchemy)
        self.when_imported("django.db", self._register_django)

    @property
    def slow(self) -> float:
        return float(self.options.get("slow", 100))

    def _ignored(self, sql: str) -> bool:
        return any(re.search(p, sql, re.I) for p in self.options.get("ignore_patterns", []))

    @safely
    def record_query(self, sql: str, params: Any, duration_ms: float, connection: str, many: bool = False) -> None:
        if not self.active or self._ignored(sql):
            return
        file, line = find_app_frame(2)
        bindings: Sequence[Any] = params[0] if many and params else params
        slow = duration_ms >= self.slow
        entry = IncomingEntry(EntryType.QUERY, {
            "connection": connection,
            "bindings": to_jsonable(bindings or [], depth=3),
            "sql": interpolate(sql, bindings),
            "time": f"{duration_ms:.2f}",
            "slow": slow,
            "file": relative_path(file),
            "line": line,
            "hash": md5(sql),
        }, family_hash=md5(sql))
        if slow:
            entry.add_tags(["slow"])
        self.tracefox.record(entry)

    # -- SQLAlchemy ------------------------------------------------------------------

    def _register_sqlalchemy(self, _module: Any) -> None:
        from sqlalchemy import event
        from sqlalchemy.engine import Engine

        @safely
        def before(conn, cursor, statement, parameters, context, executemany):
            conn.info.setdefault("tracefox_query_start", []).append(time.perf_counter())

        @safely
        def after(conn, cursor, statement, parameters, context, executemany):
            stack = conn.info.get("tracefox_query_start")
            if not stack:
                return
            started = stack.pop()
            if getattr(conn.engine, "_tracefox_storage", False):
                return
            url = conn.engine.url
            name = url.get_backend_name() + (f":{url.database}" if url.database else "")
            self.record_query(statement, parameters, (time.perf_counter() - started) * 1000, name, executemany)

        @safely
        def on_error(context):
            conn = getattr(context, "connection", None)
            if conn is not None and conn.info.get("tracefox_query_start"):
                conn.info["tracefox_query_start"].pop()

        event.listen(Engine, "before_cursor_execute", before)
        event.listen(Engine, "after_cursor_execute", after)
        event.listen(Engine, "handle_error", on_error)

        def unhook():
            event.remove(Engine, "before_cursor_execute", before)
            event.remove(Engine, "after_cursor_execute", after)
            event.remove(Engine, "handle_error", on_error)

        self.on_unregister(unhook)

    # -- Django ----------------------------------------------------------------------

    def _register_django(self, _module: Any) -> None:
        from django.conf import settings
        from django.db import connections
        from django.db.backends.signals import connection_created

        watcher = self

        def wrapper(execute, sql, params, many, context):
            started = time.perf_counter()
            try:
                return execute(sql, params, many, context)
            finally:
                db = context.get("connection")
                alias = getattr(db, "alias", "default")
                watcher.record_query(sql, params, (time.perf_counter() - started) * 1000, alias, many)

        def install(connection, **_kwargs):
            if wrapper not in connection.execute_wrappers:
                connection.execute_wrappers.append(wrapper)

        connection_created.connect(install, weak=False, dispatch_uid="tracefox_queries")
        if settings.configured:
            for conn in connections.all(initialized_only=True) if _supports_initialized_only() else []:
                install(conn)

        def unhook():
            connection_created.disconnect(dispatch_uid="tracefox_queries")
            if settings.configured:
                for conn in connections.all():
                    if wrapper in conn.execute_wrappers:
                        conn.execute_wrappers.remove(wrapper)

        self.on_unregister(unhook)


def _supports_initialized_only() -> bool:
    import inspect

    from django.db import connections

    try:
        return "initialized_only" in inspect.signature(connections.all).parameters
    except (TypeError, ValueError):
        return False
