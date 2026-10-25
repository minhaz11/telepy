from __future__ import annotations

from typing import Any, Dict, Optional

from ..context import Batch, current_batch
from ..entry import EntryType, IncomingEntry
from ..utils import class_name, model_key, to_jsonable
from .base import Watcher, safely


class ModelWatcher(Watcher):
    """Model created / updated / deleted events and per-request hydration counts (SQLAlchemy ORM, Django ORM)."""

    name = "models"

    def register(self) -> None:
        self.when_imported("sqlalchemy.orm", self._register_sqlalchemy)
        self.when_imported("django.db.models", self._register_django)

    def _tracks(self, action: str) -> bool:
        return action in self.options.get("events", ["created", "updated", "deleted"])

    @staticmethod
    def _own(instance: Any) -> bool:
        return getattr(getattr(instance, "_meta", None), "app_label", None) == "tracefox"

    @safely
    def record_action(self, action: str, instance: Any, changes: Optional[Dict[str, Any]] = None) -> None:
        if not self.active or not self._tracks(action) or self._own(instance):
            return
        key = model_key(instance) or class_name(instance)
        entry = IncomingEntry(EntryType.MODEL, {
            "action": action,
            "model": key,
            "changes": to_jsonable(changes, depth=3) if changes else None,
        })
        entry.add_tags([key])
        self.tracefox.record(entry)

    @safely
    def count_hydration(self, instance: Any) -> None:
        if not self.options.get("hydrations", True) or not self.active or self._own(instance):
            return
        batch = current_batch.get()
        if batch is None:
            return
        counts = batch.state.get("tracefox_hydrations")
        if counts is None:
            counts = batch.state["tracefox_hydrations"] = {}
            batch.finalizers.append(self._flush_hydrations)
        name = class_name(instance)
        counts[name] = counts.get(name, 0) + 1

    def _flush_hydrations(self, batch: Batch) -> None:
        for model, count in batch.state.get("tracefox_hydrations", {}).items():
            entry = IncomingEntry(EntryType.MODEL, {"action": "retrieved", "model": model, "count": count})
            entry.batch_id = batch.id
            entry.content = {"hostname": self.tracefox.hostname, **entry.content}
            batch.entries.append(entry)

    # -- SQLAlchemy ------------------------------------------------------------------

    def _register_sqlalchemy(self, orm: Any) -> None:
        from sqlalchemy import event
        from sqlalchemy import inspect as sa_inspect

        watcher = self

        def columns(target) -> Dict[str, Any]:
            state = sa_inspect(target)
            return {prop.key: state.dict.get(prop.key) for prop in state.mapper.column_attrs}

        @safely
        def after_insert(mapper, connection, target):
            watcher.record_action("created", target, columns(target))

        @safely
        def after_update(mapper, connection, target):
            state = sa_inspect(target)
            changes = {}
            for attr in state.attrs:
                history = attr.history
                if history.has_changes() and attr.key in mapper.column_attrs:
                    changes[attr.key] = history.added[0] if history.added else None
            if changes:
                watcher.record_action("updated", target, changes)

        @safely
        def after_delete(mapper, connection, target):
            watcher.record_action("deleted", target)

        @safely
        def on_load(target, context):
            watcher.count_hydration(target)

        listeners = [("after_insert", after_insert), ("after_update", after_update),
                     ("after_delete", after_delete), ("load", on_load)]
        for name, fn in listeners:
            event.listen(orm.Mapper, name, fn)

        def unhook():
            for name, fn in listeners:
                event.remove(orm.Mapper, name, fn)

        self.on_unregister(unhook)

    # -- Django ----------------------------------------------------------------------

    def _register_django(self, models: Any) -> None:
        from django.db.models import signals

        watcher = self

        def snapshot(instance) -> Dict[str, Any]:
            return {f.attname: instance.__dict__[f.attname] for f in instance._meta.concrete_fields if f.attname in instance.__dict__}

        original_from_db = models.Model.__dict__["from_db"]

        def from_db(cls, *args, **kwargs):
            instance = original_from_db.__func__(cls, *args, **kwargs)
            try:
                instance._tracefox_original = snapshot(instance)
                watcher.count_hydration(instance)
            except Exception:
                pass
            return instance

        models.Model.from_db = classmethod(from_db)
        self._patches.append((models.Model, "from_db", original_from_db))

        def post_save(sender, instance, created, **kwargs):
            if created:
                watcher.record_action("created", instance, snapshot(instance))
            else:
                before = getattr(instance, "_tracefox_original", None)
                current = snapshot(instance)
                changes = current if before is None else {k: v for k, v in current.items() if before.get(k, v) != v}
                if changes:
                    watcher.record_action("updated", instance, changes)
            instance._tracefox_original = snapshot(instance)

        def post_delete(sender, instance, **kwargs):
            watcher.record_action("deleted", instance)

        signals.post_save.connect(post_save, weak=False, dispatch_uid="tracefox_models_save")
        signals.post_delete.connect(post_delete, weak=False, dispatch_uid="tracefox_models_delete")

        def unhook():
            signals.post_save.disconnect(dispatch_uid="tracefox_models_save")
            signals.post_delete.disconnect(dispatch_uid="tracefox_models_delete")

        self.on_unregister(unhook)
