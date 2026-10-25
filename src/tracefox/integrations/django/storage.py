from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from django.conf import settings
from django.db import close_old_connections, transaction
from django.db.models import Count

from ...entry import EntryType, EntryUpdate, IncomingEntry
from ...storage.base import EntryQueryOptions, Storage
from ...utils import json_dumps


def _db_time(value: datetime) -> datetime:
    value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc) if settings.USE_TZ else value.astimezone(timezone.utc).replace(tzinfo=None)


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class DjangoStorage(Storage):
    """Stores entries in the project's own database through the ORM; tables come from ``manage.py migrate``."""

    def __init__(self, using: str = "default"):
        self.using = using

    @property
    def _models(self):
        from . import models

        return models

    def _entries(self):
        return self._models.Entry.objects.using(self.using)

    def _tags(self):
        return self._models.EntryTag.objects.using(self.using)

    def migrate(self) -> None:
        from django.core.management import call_command

        call_command("migrate", "tracefox", database=self.using, verbosity=0)

    def prepare_thread(self) -> None:
        # The writer thread holds its own connection; drop it once it is stale or broken.
        close_old_connections()

    # -- writing ---------------------------------------------------------------

    def store(self, entries: List[IncomingEntry]) -> None:
        if not entries:
            return
        m = self._models
        with transaction.atomic(using=self.using):
            for entry in entries:
                if entry.type == EntryType.EXCEPTION and entry.family_hash:
                    family = self._entries().filter(type=EntryType.EXCEPTION, family_hash=entry.family_hash)
                    entry.content["occurrences"] = family.count() + 1
                    family.update(should_display_on_index=False)
            m.Entry.objects.using(self.using).bulk_create([
                m.Entry(
                    uuid=e.uuid,
                    batch_id=e.batch_id or e.uuid,
                    family_hash=e.family_hash,
                    should_display_on_index=e.should_display_on_index,
                    type=e.type,
                    content=json_dumps(e.content),
                    created_at=_db_time(e.recorded_at),
                )
                for e in entries
            ])
            tags = [m.EntryTag(entry_uuid=e.uuid, tag=t[:191]) for e in entries for t in dict.fromkeys(e.tags)]
            if tags:
                m.EntryTag.objects.using(self.using).bulk_create(tags, ignore_conflicts=True)

    def update(self, updates: List[EntryUpdate]) -> List[EntryUpdate]:
        missing: List[EntryUpdate] = []
        m = self._models
        with transaction.atomic(using=self.using):
            for update in updates:
                entry = self._entries().select_for_update().filter(uuid=update.uuid, type=update.type).first()
                if entry is None:
                    missing.append(update)
                    continue
                content = json.loads(entry.content)
                content.update(json.loads(json_dumps(update.changes)))
                for key, delta in update.increments.items():
                    content[key] = (content.get(key) or 0) + delta
                entry.content = json_dumps(content)
                entry.save(using=self.using, update_fields=["content"])
                if update.tags_removed:
                    self._tags().filter(entry_uuid=update.uuid, tag__in=update.tags_removed).delete()
                if update.tags_added:
                    m.EntryTag.objects.using(self.using).bulk_create(
                        [m.EntryTag(entry_uuid=update.uuid, tag=t) for t in dict.fromkeys(update.tags_added)],
                        ignore_conflicts=True,
                    )
        return missing

    # -- reading ---------------------------------------------------------------

    def _row(self, entry, tags: Optional[List[str]] = None) -> Dict[str, Any]:
        data = {
            "id": entry.uuid,
            "sequence": entry.sequence,
            "batch_id": entry.batch_id,
            "family_hash": entry.family_hash,
            "type": entry.type,
            "content": json.loads(entry.content),
            "created_at": _iso(entry.created_at),
        }
        if tags is not None:
            data["tags"] = tags
        return data

    def get(self, type: Optional[str], options: EntryQueryOptions) -> List[Dict[str, Any]]:
        qs = self._entries()
        if type:
            qs = qs.filter(type=type)
        if options.family_hash:
            qs = qs.filter(family_hash=options.family_hash)
        elif not options.batch_id and not options.uuids:
            qs = qs.filter(should_display_on_index=True)
        if options.batch_id:
            qs = qs.filter(batch_id=options.batch_id)
        if options.uuids:
            qs = qs.filter(uuid__in=options.uuids)
        if options.before_sequence:
            qs = qs.filter(sequence__lt=options.before_sequence)
        if options.after_sequence:
            qs = qs.filter(sequence__gt=options.after_sequence)
        if options.tag:
            qs = qs.filter(uuid__in=self._tags().filter(tag=options.tag).values("entry_uuid"))
        limit = max(1, min(int(options.limit or 50), 1000))
        return [self._row(e) for e in qs.order_by("-sequence")[:limit]]

    def find(self, uuid: str) -> Optional[Dict[str, Any]]:
        entry = self._entries().filter(uuid=uuid).first()
        if entry is None:
            return None
        return self._row(entry, list(self._tags().filter(entry_uuid=uuid).values_list("tag", flat=True)))

    def batch_entries(self, batch_ids: Iterable[str]) -> List[Dict[str, Any]]:
        ids = [b for b in dict.fromkeys(batch_ids) if b]
        if not ids:
            return []
        return [self._row(e) for e in self._entries().filter(batch_id__in=ids).order_by("-sequence")]

    def counts(self) -> Dict[str, int]:
        rows = self._entries().filter(should_display_on_index=True).values("type").annotate(n=Count("sequence"))
        return {r["type"]: r["n"] for r in rows}

    # -- monitoring & settings ---------------------------------------------------

    def monitored_tags(self) -> List[str]:
        return sorted(self._models.MonitoredTag.objects.using(self.using).values_list("tag", flat=True))

    def monitor(self, tags: Iterable[str]) -> None:
        m = self._models
        clean = [t.strip()[:191] for t in tags if t and t.strip()]
        m.MonitoredTag.objects.using(self.using).bulk_create([m.MonitoredTag(tag=t) for t in dict.fromkeys(clean)], ignore_conflicts=True)

    def stop_monitoring(self, tags: Iterable[str]) -> None:
        self._models.MonitoredTag.objects.using(self.using).filter(tag__in=list(tags)).delete()

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self._models.Setting.objects.using(self.using).filter(key=key).first()
        return default if row is None or row.value is None else row.value

    def set_setting(self, key: str, value: Optional[str]) -> None:
        settings_qs = self._models.Setting.objects.using(self.using)
        if value is None:
            settings_qs.filter(key=key).delete()
        else:
            settings_qs.update_or_create(key=key, defaults={"value": value})

    # -- maintenance -------------------------------------------------------------

    def prune(self, before: datetime, keep_exceptions: bool = False) -> int:
        qs = self._entries().filter(created_at__lt=_db_time(before))
        if keep_exceptions:
            qs = qs.exclude(type=EntryType.EXCEPTION)
        with transaction.atomic(using=self.using):
            self._tags().filter(entry_uuid__in=qs.values("uuid")).delete()
            deleted, _ = qs.delete()
        return deleted

    def clear(self) -> None:
        with transaction.atomic(using=self.using):
            self._tags().all().delete()
            self._entries().all().delete()
