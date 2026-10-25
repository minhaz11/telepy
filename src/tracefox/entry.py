from __future__ import annotations

import os
import time
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class EntryType:
    BATCH = "batch"
    CACHE = "cache"
    CLIENT_REQUEST = "client_request"
    COMMAND = "command"
    DUMP = "dump"
    EVENT = "event"
    EXCEPTION = "exception"
    GATE = "gate"
    JOB = "job"
    LOG = "log"
    MAIL = "mail"
    MODEL = "model"
    NOTIFICATION = "notification"
    QUERY = "query"
    REDIS = "redis"
    REQUEST = "request"
    SCHEDULED_TASK = "schedule"
    VIEW = "view"

    ALL = (
        BATCH, CACHE, CLIENT_REQUEST, COMMAND, DUMP, EVENT, EXCEPTION, GATE, JOB,
        LOG, MAIL, MODEL, NOTIFICATION, QUERY, REDIS, REQUEST, SCHEDULED_TASK, VIEW,
    )


def ordered_uuid() -> str:
    # Time-prefixed like Laravel's Str::orderedUuid so uuids sort by creation time.
    ms = int(time.time() * 1000)
    rand = os.urandom(10)
    raw = ms.to_bytes(6, "big") + rand
    return str(_uuid.UUID(bytes=raw))


@dataclass
class IncomingEntry:
    type: str
    content: Dict[str, Any]
    uuid: str = field(default_factory=ordered_uuid)
    batch_id: Optional[str] = None
    family_hash: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    should_display_on_index: bool = True
    recorded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def set_user(self, user: Optional[Dict[str, Any]]) -> "IncomingEntry":
        if user:
            self.content["user"] = user
            if user.get("id") is not None:
                self.add_tags([f"Auth:{user['id']}"])
        return self

    def add_tags(self, tags) -> "IncomingEntry":
        for tag in tags or ():
            tag = str(tag)
            if tag and tag not in self.tags:
                self.tags.append(tag)
        return self

    def is_request(self) -> bool:
        return self.type == EntryType.REQUEST

    def is_failed_request(self) -> bool:
        return self.is_request() and int(self.content.get("response_status") or 0) >= 500

    def is_exception(self) -> bool:
        return self.type == EntryType.EXCEPTION

    def is_reportable_exception(self) -> bool:
        return self.is_exception()

    def is_failed_job(self) -> bool:
        return self.type == EntryType.JOB and self.content.get("status") == "failed"

    def is_scheduled_task(self) -> bool:
        return self.type == EntryType.SCHEDULED_TASK

    def is_slow_query(self) -> bool:
        return self.type == EntryType.QUERY and bool(self.content.get("slow"))

    def is_dump(self) -> bool:
        return self.type == EntryType.DUMP

    def has_monitored_tag(self, monitored) -> bool:
        return bool(set(self.tags) & set(monitored or ()))

    def to_row(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "batch_id": self.batch_id,
            "family_hash": self.family_hash,
            "should_display_on_index": self.should_display_on_index,
            "type": self.type,
            "content": self.content,
            "created_at": self.recorded_at,
            "tags": list(self.tags),
        }


@dataclass
class EntryUpdate:
    """A change to an already stored entry (e.g. a pending job that later finished)."""

    uuid: str
    type: str
    changes: Dict[str, Any] = field(default_factory=dict)
    increments: Dict[str, int] = field(default_factory=dict)
    tags_added: List[str] = field(default_factory=list)
    tags_removed: List[str] = field(default_factory=list)
