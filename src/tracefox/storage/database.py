from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from ..entry import EntryType, EntryUpdate, IncomingEntry
from ..utils import json_dumps
from .base import EntryQueryOptions, Storage

metadata = sa.MetaData()

entries_table = sa.Table(
    "tracefox_entries",
    metadata,
    sa.Column("sequence", sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True, autoincrement=True),
    sa.Column("uuid", sa.String(36), nullable=False, unique=True),
    sa.Column("batch_id", sa.String(36), nullable=False),
    sa.Column("family_hash", sa.String(64), nullable=True),
    sa.Column("should_display_on_index", sa.Boolean, nullable=False, default=True),
    sa.Column("type", sa.String(20), nullable=False),
    sa.Column("content", sa.Text().with_variant(mysql.LONGTEXT(), "mysql"), nullable=False),
    sa.Column("created_at", sa.DateTime, nullable=True),
    sa.Index("tracefox_entries_batch_id_index", "batch_id"),
    sa.Index("tracefox_entries_family_hash_index", "family_hash"),
    sa.Index("tracefox_entries_created_at_index", "created_at"),
    sa.Index("tracefox_entries_type_should_display_on_index_index", "type", "should_display_on_index"),
)

tags_table = sa.Table(
    "tracefox_entries_tags",
    metadata,
    sa.Column("entry_uuid", sa.String(36), primary_key=True),
    sa.Column("tag", sa.String(191), primary_key=True),
    sa.Index("tracefox_entries_tags_tag_index", "tag"),
)

monitoring_table = sa.Table(
    "tracefox_monitoring",
    metadata,
    sa.Column("tag", sa.String(191), primary_key=True),
)

settings_table = sa.Table(
    "tracefox_settings",
    metadata,
    sa.Column("key", sa.String(191), primary_key=True),
    sa.Column("value", sa.Text, nullable=True),
)


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


_SYNC_DRIVERS = {
    "postgresql+asyncpg": ("postgresql+psycopg", "postgresql+psycopg2"),
    "postgresql+psycopg_async": ("postgresql+psycopg",),
    "mysql+aiomysql": ("mysql+pymysql", "mysql+mysqldb"),
    "mysql+asyncmy": ("mysql+pymysql", "mysql+mysqldb"),
    "sqlite+aiosqlite": ("sqlite",),
    "mssql+aioodbc": ("mssql+pyodbc",),
    "oracle+oracledb_async": ("oracle+oracledb",),
}


def sync_url(url: sa.engine.URL) -> sa.engine.URL:
    """The blocking-driver twin of an async URL (the writer thread can't drive asyncpg & co.)."""
    for candidate in _SYNC_DRIVERS.get(url.drivername, ()):
        sync = url.set(drivername=candidate)
        try:
            sync.get_dialect().import_dbapi()
        except Exception:
            continue
        return sync
    raise RuntimeError(
        f"Tracefox writes from a background thread and needs a synchronous driver for {url.drivername}. "
        f"Install one of {', '.join(_SYNC_DRIVERS.get(url.drivername, ('a sync driver',)))} or pass database_url."
    )


class DatabaseStorage(Storage):
    """SQLAlchemy storage: the application's own engine (``engine=``) or a separate ``database_url``."""

    def __init__(self, url: Optional[str] = None, engine_options: Optional[Dict[str, Any]] = None,
                 auto_migrate: bool = True, engine: Any = None):
        if url is None and engine is None:
            raise ValueError("DatabaseStorage needs a url or an engine")
        self.url = url
        self.engine_options = dict(engine_options or {})
        self.auto_migrate = auto_migrate
        self._source = engine
        self._engine: Optional[sa.engine.Engine] = None
        self._pid: Optional[int] = None
        self._lock = threading.Lock()
        self._migrated = False

    @property
    def engine(self) -> sa.engine.Engine:
        if self._engine is None or (self._pid != os.getpid() and self._source is None):
            with self._lock:
                if self._engine is None or (self._pid != os.getpid() and self._source is None):
                    if self._engine is not None:
                        self._engine.dispose(close=False)
                    self._engine = self._resolve_engine()
                    self._pid = os.getpid()
                    if self.auto_migrate and not self._migrated:
                        self.migrate()
        return self._engine

    def _resolve_engine(self) -> sa.engine.Engine:
        if self._source is None:
            return self._create_engine(self.url)
        engine = self._source() if callable(self._source) and not isinstance(self._source, sa.engine.Engine) else self._source
        sync_engine = getattr(engine, "sync_engine", None)
        if sync_engine is not None:
            # An AsyncEngine: same database, but through a blocking driver owned by Tracefox.
            return self._create_engine(sync_url(engine.url).render_as_string(hide_password=False))
        if not isinstance(engine, sa.engine.Engine):
            raise TypeError(f"Tracefox engine must be a SQLAlchemy Engine or AsyncEngine, got {type(engine).__name__}")
        # The app's own engine: Tracefox's queries are skipped because they run with recording suspended.
        return engine

    def _create_engine(self, url_string: str) -> sa.engine.Engine:
        options = dict(self.engine_options)
        url = sa.engine.make_url(url_string)
        if url.get_backend_name() == "sqlite":
            options.setdefault("connect_args", {}).setdefault("check_same_thread", False)
            if url.database in (None, "", ":memory:"):
                options.setdefault("poolclass", sa.pool.StaticPool)
        engine = sa.create_engine(url, **options)
        engine._tracefox_storage = True
        if url.get_backend_name() == "sqlite":
            @sa.event.listens_for(engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record):
                cursor = dbapi_conn.cursor()
                try:
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA synchronous=NORMAL")
                    cursor.execute("PRAGMA busy_timeout=5000")
                finally:
                    cursor.close()
        return engine

    def owns(self, engine: Any) -> bool:
        return engine is not None and engine is self._engine

    def reset_after_fork(self) -> None:
        if self._source is None:
            self._engine = None

    def migrate(self) -> None:
        engine = self._engine if self._engine is not None else self.engine
        metadata.create_all(engine, checkfirst=True)
        self._migrated = True

    # -- writing ---------------------------------------------------------------

    def store(self, entries: List[IncomingEntry]) -> None:
        if not entries:
            return
        with self.engine.begin() as conn:
            for entry in entries:
                if entry.type == EntryType.EXCEPTION and entry.family_hash:
                    self._store_exception_family(conn, entry)
            conn.execute(
                entries_table.insert(),
                [
                    {
                        "uuid": e.uuid,
                        "batch_id": e.batch_id or e.uuid,
                        "family_hash": e.family_hash,
                        "should_display_on_index": e.should_display_on_index,
                        "type": e.type,
                        "content": json_dumps(e.content),
                        "created_at": _naive_utc(e.recorded_at),
                    }
                    for e in entries
                ],
            )
            tag_rows = [{"entry_uuid": e.uuid, "tag": t[:191]} for e in entries for t in dict.fromkeys(e.tags)]
            if tag_rows:
                conn.execute(tags_table.insert(), tag_rows)

    def _store_exception_family(self, conn, entry: IncomingEntry) -> None:
        occurrences = conn.execute(
            sa.select(sa.func.count()).select_from(entries_table).where(
                entries_table.c.type == EntryType.EXCEPTION,
                entries_table.c.family_hash == entry.family_hash,
            )
        ).scalar_one()
        conn.execute(
            entries_table.update()
            .where(entries_table.c.type == EntryType.EXCEPTION, entries_table.c.family_hash == entry.family_hash)
            .values(should_display_on_index=False)
        )
        entry.content["occurrences"] = occurrences + 1

    def update(self, updates: List[EntryUpdate]) -> List[EntryUpdate]:
        """Apply updates; returns the ones whose entry isn't stored yet."""
        missing: List[EntryUpdate] = []
        if not updates:
            return missing
        with self.engine.begin() as conn:
            for update in updates:
                row = conn.execute(
                    sa.select(entries_table.c.content).where(
                        entries_table.c.uuid == update.uuid, entries_table.c.type == update.type
                    )
                ).first()
                if row is None:
                    missing.append(update)
                    continue
                content = json.loads(row.content)
                content.update(json.loads(json_dumps(update.changes)))
                for key, delta in update.increments.items():
                    content[key] = (content.get(key) or 0) + delta
                conn.execute(
                    entries_table.update().where(entries_table.c.uuid == update.uuid).values(content=json_dumps(content))
                )
                if update.tags_removed:
                    conn.execute(
                        tags_table.delete().where(
                            tags_table.c.entry_uuid == update.uuid, tags_table.c.tag.in_(update.tags_removed)
                        )
                    )
                if update.tags_added:
                    existing = set(
                        conn.execute(sa.select(tags_table.c.tag).where(tags_table.c.entry_uuid == update.uuid)).scalars()
                    )
                    new = [{"entry_uuid": update.uuid, "tag": t} for t in dict.fromkeys(update.tags_added) if t not in existing]
                    if new:
                        conn.execute(tags_table.insert(), new)
        return missing

    # -- reading ---------------------------------------------------------------

    def _row(self, row, tags: Optional[List[str]] = None) -> Dict[str, Any]:
        data = {
            "id": row.uuid,
            "sequence": row.sequence,
            "batch_id": row.batch_id,
            "family_hash": row.family_hash,
            "type": row.type,
            "content": json.loads(row.content),
            "created_at": _iso(row.created_at),
        }
        if tags is not None:
            data["tags"] = tags
        return data

    def get(self, type: Optional[str], options: EntryQueryOptions) -> List[Dict[str, Any]]:
        t = entries_table
        query = sa.select(t)
        if type:
            query = query.where(t.c.type == type)
        if options.family_hash:
            query = query.where(t.c.family_hash == options.family_hash)
        elif not options.batch_id and not options.uuids:
            query = query.where(t.c.should_display_on_index == sa.true())
        if options.batch_id:
            query = query.where(t.c.batch_id == options.batch_id)
        if options.uuids:
            query = query.where(t.c.uuid.in_(options.uuids))
        if options.before_sequence:
            query = query.where(t.c.sequence < options.before_sequence)
        if options.after_sequence:
            query = query.where(t.c.sequence > options.after_sequence)
        if options.tag:
            query = query.where(
                t.c.uuid.in_(sa.select(tags_table.c.entry_uuid).where(tags_table.c.tag == options.tag))
            )
        query = query.order_by(t.c.sequence.desc()).limit(max(1, min(int(options.limit or 50), 1000)))
        with self.engine.connect() as conn:
            return [self._row(r) for r in conn.execute(query)]

    def find(self, uuid: str) -> Optional[Dict[str, Any]]:
        with self.engine.connect() as conn:
            row = conn.execute(sa.select(entries_table).where(entries_table.c.uuid == uuid)).first()
            if row is None:
                return None
            tags = list(conn.execute(sa.select(tags_table.c.tag).where(tags_table.c.entry_uuid == uuid)).scalars())
            return self._row(row, tags)

    def batch_entries(self, batch_ids: Iterable[str]) -> List[Dict[str, Any]]:
        ids = [b for b in dict.fromkeys(batch_ids) if b]
        if not ids:
            return []
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa.select(entries_table).where(entries_table.c.batch_id.in_(ids)).order_by(entries_table.c.sequence.desc())
            )
            return [self._row(r) for r in rows]

    def counts(self) -> Dict[str, int]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa.select(entries_table.c.type, sa.func.count())
                .where(entries_table.c.should_display_on_index == sa.true())
                .group_by(entries_table.c.type)
            )
            return {r[0]: r[1] for r in rows}

    # -- monitoring & settings ---------------------------------------------------

    def monitored_tags(self) -> List[str]:
        with self.engine.connect() as conn:
            return sorted(conn.execute(sa.select(monitoring_table.c.tag)).scalars())

    def monitor(self, tags: Iterable[str]) -> None:
        tags = [t.strip()[:191] for t in tags if t and t.strip()]
        with self.engine.begin() as conn:
            existing = set(conn.execute(sa.select(monitoring_table.c.tag)).scalars())
            new = [{"tag": t} for t in dict.fromkeys(tags) if t not in existing]
            if new:
                conn.execute(monitoring_table.insert(), new)

    def stop_monitoring(self, tags: Iterable[str]) -> None:
        with self.engine.begin() as conn:
            conn.execute(monitoring_table.delete().where(monitoring_table.c.tag.in_(list(tags))))

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self.engine.connect() as conn:
            value = conn.execute(sa.select(settings_table.c.value).where(settings_table.c.key == key)).scalar()
            return default if value is None else value

    def set_setting(self, key: str, value: Optional[str]) -> None:
        with self.engine.begin() as conn:
            conn.execute(settings_table.delete().where(settings_table.c.key == key))
            if value is not None:
                conn.execute(settings_table.insert().values(key=key, value=value))

    # -- maintenance -------------------------------------------------------------

    def prune(self, before: datetime, keep_exceptions: bool = False) -> int:
        condition = entries_table.c.created_at < _naive_utc(before)
        if keep_exceptions:
            condition = sa.and_(condition, entries_table.c.type != EntryType.EXCEPTION)
        with self.engine.begin() as conn:
            conn.execute(
                tags_table.delete().where(tags_table.c.entry_uuid.in_(sa.select(entries_table.c.uuid).where(condition)))
            )
            return conn.execute(entries_table.delete().where(condition)).rowcount or 0

    def clear(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(tags_table.delete())
            conn.execute(entries_table.delete())
