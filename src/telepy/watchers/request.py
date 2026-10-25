from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..entry import EntryType, IncomingEntry
from ..utils import hide, memory_usage_mb, path_matches, to_jsonable
from .base import Watcher


class RequestWatcher(Watcher):
    """Requests are captured by the framework integrations, which call ``record_request``."""

    name = "requests"

    @property
    def size_limit(self) -> int:
        return int(self.options.get("size_limit", 64))

    def should_ignore(self, path: str, method: str = "GET") -> bool:
        config = self.telepy.config
        base = config.path.rstrip("/")
        if path == base or path.startswith(base + "/"):
            return True
        if method.upper() in {m.upper() for m in self.options.get("ignore_http_methods", [])}:
            return True
        if config.only_paths and not path_matches(path, config.only_paths):
            return True
        return path_matches(path, config.ignore_paths)

    def record_request(
        self,
        *,
        method: str,
        uri: str,
        ip_address: Optional[str],
        headers: Dict[str, str],
        payload: Any,
        response_status: int,
        response_headers: Dict[str, str],
        response: Any,
        duration: float,
        session: Any = None,
        controller_action: Optional[str] = None,
        middleware: Optional[List[str]] = None,
        user: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.active:
            return
        if response_status in set(self.options.get("ignore_status_codes", [])):
            return
        config = self.telepy.config
        entry = IncomingEntry(EntryType.REQUEST, {
            "ip_address": ip_address,
            "uri": uri or "/",
            "method": method.upper(),
            "controller_action": controller_action,
            "middleware": middleware or [],
            "headers": hide(dict(headers), config.hidden_request_headers),
            "payload": hide(to_jsonable(payload, depth=8), config.hidden_request_parameters),
            "session": hide(to_jsonable(session or {}, depth=6), config.hidden_request_parameters),
            "response_headers": dict(response_headers),
            "response_status": int(response_status),
            "response": hide(to_jsonable(response, depth=10), config.hidden_response_parameters),
            "duration": int(round(duration)),
            "memory": memory_usage_mb(),
        })
        batch = self.telepy.current_batch()
        user = user or (batch.user if batch else None)
        if user:
            entry.set_user(user)
            if batch is not None:
                batch.user = user
        self.telepy.record(entry)
