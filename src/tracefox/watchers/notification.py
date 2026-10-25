from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..entry import EntryType, IncomingEntry
from ..utils import class_name, model_key, to_jsonable
from .base import Watcher, safely

if TYPE_CHECKING:
    from ..core import Tracefox


class NotificationWatcher(Watcher):
    """Python has no standard notification layer, so notifications are recorded explicitly."""

    name = "notifications"

    @staticmethod
    def record_notification(tracefox: "Tracefox", notification: Any, notifiable: Any, channel: str,
                            response: Any = None, queued: bool = False) -> None:
        watcher = tracefox.watcher("notifications")
        if watcher is not None:
            watcher.record(notification, notifiable, channel, response, queued)

    @safely
    def record(self, notification: Any, notifiable: Any, channel: str, response: Any = None, queued: bool = False) -> None:
        if not self.active:
            return
        name = notification if isinstance(notification, str) else class_name(notification)
        target = notifiable if isinstance(notifiable, str) else (model_key(notifiable) or str(notifiable))
        entry = IncomingEntry(EntryType.NOTIFICATION, {
            "notification": name,
            "queued": queued,
            "notifiable": target,
            "channel": channel,
            "response": to_jsonable(response, depth=4),
        })
        entry.add_tags([target])
        self.tracefox.record(entry)
