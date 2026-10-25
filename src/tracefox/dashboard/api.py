from __future__ import annotations

import base64
import hmac
import html
import json
import logging
import mimetypes
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from ..entry import EntryType
from ..storage import EntryQueryOptions

if TYPE_CHECKING:
    from ..core import Tracefox

logger = logging.getLogger("tracefox")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
CSRF_HEADER = "x-tracefox-request"


@dataclass
class DashboardRequest:
    method: str
    path: str
    query: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    headers: Dict[str, str] = field(default_factory=dict)
    base_path: str = "/tracefox"
    native: Any = None

    def json(self) -> Dict[str, Any]:
        try:
            return json.loads(self.body.decode("utf-8") or "{}")
        except Exception:
            return {}


@dataclass
class DashboardResponse:
    status: int
    body: bytes
    headers: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def content_type(self) -> str:
        return next((v for k, v in self.headers if k.lower() == "content-type"), "text/plain")


def _json(data: Any, status: int = 200) -> DashboardResponse:
    return DashboardResponse(status, json.dumps(data, default=str).encode(), [
        ("content-type", "application/json"), ("cache-control", "no-store"),
    ])


def _html(text: str, status: int = 200) -> DashboardResponse:
    return DashboardResponse(status, text.encode(), [("content-type", "text/html; charset=utf-8"), ("cache-control", "no-store")])


class Dashboard:
    """Framework-agnostic dashboard: the ASGI / WSGI / Django adapters translate to and from this."""

    def __init__(self, tracefox: "Tracefox"):
        self.tracefox = tracefox

    def access(self, request: DashboardRequest) -> Optional[str]:
        """None when the viewer may see the dashboard, else "login" (ask for credentials) or "forbidden"."""
        config = self.tracefox.config
        if config.dashboard_is_open:
            return None
        users = config.basic_auth_users()
        if users and not _valid_basic_auth(request.headers.get("authorization", ""), users):
            return "login"
        if config.authorization is not None:
            try:
                allowed = bool(config.authorization(request.native if request.native is not None else request))
            except Exception:
                logger.exception("Tracefox authorization callback failed")
                allowed = False
            return None if allowed else "forbidden"
        return None if users else "forbidden"

    def authorize(self, request: DashboardRequest) -> bool:
        return self.access(request) is None

    def handle(self, request: DashboardRequest) -> DashboardResponse:
        with self.tracefox.without_recording():
            try:
                return self._handle(request)
            except Exception as exc:
                logger.exception("Tracefox dashboard error")
                return _json({"message": f"{type(exc).__name__}: {exc}"}, 500)

    def _handle(self, request: DashboardRequest) -> DashboardResponse:
        path = "/" + request.path.lstrip("/")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        denied = self.access(request)
        if denied == "login":
            challenge = [("www-authenticate", 'Basic realm="TraceFox", charset="UTF-8"')]
            if path.startswith("/api/"):
                response = _json({"message": "Login required."}, 401)
            else:
                response = _html(LOGIN_PAGE, 401)
            response.headers.extend(challenge)
            return response
        if denied == "forbidden":
            if path.startswith("/api/"):
                return _json({"message": "Unauthorized."}, 403)
            return _html(FORBIDDEN_PAGE.replace("{{ environment }}", html.escape(self.tracefox.config.env)), 403)
        if path.startswith("/api/"):
            if request.method not in ("GET", "HEAD") and request.headers.get(CSRF_HEADER) != "1":
                return _json({"message": "Missing X-Tracefox-Request header."}, 419)
            return self._api(request, path[len("/api"):])
        return self._index(request)

    # -- pages -----------------------------------------------------------------------

    def _index(self, request: DashboardRequest) -> DashboardResponse:
        with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as fh:
            template = fh.read()
        from .. import __version__

        base = request.base_path.rstrip("/")
        config = {
            "path": base,
            "recording": self.tracefox.is_recording(),
            "version": __version__,
            "watchers": [name for name in self.tracefox.config.watchers if self.tracefox.config.watcher_enabled(name)],
        }
        page = template.replace("{{ base }}", html.escape(base)).replace(
            "{{ config }}", json.dumps(config).replace("</", "<\\/")
        )
        return _html(page)

    def _static(self, name: str) -> DashboardResponse:
        if not re.fullmatch(r"[\w.-]+", name):
            return _json({"message": "Not found."}, 404)
        file = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(file):
            return _json({"message": "Not found."}, 404)
        with open(file, "rb") as fh:
            body = fh.read()
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if mime.startswith("text/") or mime.endswith("javascript"):
            mime += "; charset=utf-8"
        return DashboardResponse(200, body, [("content-type", mime), ("cache-control", "no-cache")])

    # -- api -------------------------------------------------------------------------

    def _api(self, request: DashboardRequest, path: str) -> DashboardResponse:
        storage = self.tracefox.storage
        method = request.method
        parts = [p for p in path.split("/") if p]

        if parts == ["status"] and method == "GET":
            return _json({"recording": self.tracefox.is_recording(), "counts": storage.counts()})

        if parts == ["toggle-recording"] and method == "POST":
            if self.tracefox.is_recording():
                self.tracefox.pause_recording()
            else:
                self.tracefox.resume_recording()
            return _json({"recording": self.tracefox.is_recording()})

        if parts == ["entries"] and method == "DELETE":
            self.tracefox.clear()
            return _json({"cleared": True})

        if parts == ["monitored-tags"]:
            if method == "GET":
                return _json({"tags": storage.monitored_tags()})
            if method == "POST":
                tag = str(request.json().get("tag", "")).strip()
                if tag:
                    storage.monitor([tag])
                    self.tracefox.forget_monitored_cache()
                return _json({"tags": storage.monitored_tags()})
            if method == "DELETE":
                storage.stop_monitoring([str(request.json().get("tag", ""))])
                self.tracefox.forget_monitored_cache()
                return _json({"tags": storage.monitored_tags()})

        if len(parts) == 2 and parts[0] == "entries" and method == "GET":
            return self._index_entries(request, parts[1])

        if len(parts) == 3 and parts[0] == "entries" and method == "GET":
            return self._show_entry(parts[1], parts[2])

        if len(parts) == 3 and parts[0] == "mail" and method == "GET":
            return self._mail(parts[1], parts[2])

        return _json({"message": "Not found."}, 404)

    def _index_entries(self, request: DashboardRequest, type: str) -> DashboardResponse:
        if type not in EntryType.ALL and type != "all":
            return _json({"message": "Unknown entry type."}, 404)
        q = request.query

        def as_int(name: str) -> Optional[int]:
            try:
                return int(q[name]) if q.get(name) else None
            except ValueError:
                return None

        options = EntryQueryOptions(
            before_sequence=as_int("before"),
            after_sequence=as_int("after"),
            tag=q.get("tag") or None,
            family_hash=q.get("family_hash") or None,
            batch_id=q.get("batch_id") or None,
            limit=as_int("take") or 50,
        )
        entries = self.tracefox.storage.get(None if type == "all" else type, options)
        return _json({"entries": entries, "status": "enabled" if self.tracefox.is_recording() else "paused"})

    def _show_entry(self, type: str, uuid: str) -> DashboardResponse:
        entry = self.tracefox.storage.find(uuid)
        if entry is None or entry["type"] != type:
            return _json({"message": "Entry not found."}, 404)
        batch_ids = [entry["batch_id"]]
        processing = entry["content"].get("processing_batch_id")
        if processing:
            batch_ids.append(processing)
        batch = [e for e in self.tracefox.storage.batch_entries(batch_ids) if e["id"] != uuid]
        return _json({"entry": entry, "batch": batch})

    def _mail(self, uuid: str, action: str) -> DashboardResponse:
        entry = self.tracefox.storage.find(uuid)
        if entry is None or entry["type"] != EntryType.MAIL:
            return _json({"message": "Entry not found."}, 404)
        content = entry["content"]
        if action == "preview":
            return DashboardResponse(200, (content.get("html") or "").encode(), [
                ("content-type", "text/html; charset=utf-8"),
                ("content-security-policy", "default-src 'none'; img-src * data:; style-src 'unsafe-inline' *; font-src *"),
            ])
        if action == "download":
            filename = re.sub(r"[^\w.-]+", "_", content.get("subject") or "mail")[:80] or "mail"
            return DashboardResponse(200, (content.get("raw") or "").encode(), [
                ("content-type", "message/rfc822"),
                ("content-disposition", f'attachment; filename="{filename}.eml"'),
            ])
        return _json({"message": "Not found."}, 404)


def _valid_basic_auth(header: str, users: Dict[str, str]) -> bool:
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "basic" or not value:
        return False
    try:
        username, _, password = base64.b64decode(value.strip()).decode("utf-8").partition(":")
    except Exception:
        return False
    valid = False
    for expected_user, expected_password in users.items():
        # Compare every account in constant time so timing reveals nothing.
        user_ok = hmac.compare_digest(username.encode(), expected_user.encode())
        password_ok = hmac.compare_digest(password.encode(), expected_password.encode())
        valid |= user_ok and password_ok
    return valid


_PAGE_STYLE = """<style>body{font-family:system-ui,sans-serif;background:#f3f4f6;color:#111827;display:flex;align-items:center;
justify-content:center;min-height:100vh;margin:0}.card{background:#fff;padding:2rem 2.5rem;border-radius:12px;
box-shadow:0 1px 3px rgba(0,0,0,.1);max-width:560px}code{background:#e8efff;padding:.1rem .35rem;border-radius:4px}
li{margin:.35rem 0}</style>"""

LOGIN_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>TraceFox - Login required</title>""" + _PAGE_STYLE + """
</head><body><div class="card"><h2>401 &mdash; Login required</h2>
<p>Sign in with the TraceFox username and password to open the dashboard.</p></div></body></html>"""

FORBIDDEN_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>TraceFox - Forbidden</title>""" + _PAGE_STYLE + """
</head><body><div class="card"><h2>403 &mdash; TraceFox is locked</h2>
<p>The dashboard is open without login only in the <code>local</code> environment.
This app is running in <code>{{ environment }}</code>. To let people in, either:</p>
<ul>
<li>set <code>TRACEFOX_USERNAME</code> and <code>TRACEFOX_PASSWORD</code> (or the <code>basic_auth</code> option) for a login prompt, or</li>
<li>pass an <code>authorization</code> callback that returns <code>True</code> for allowed users.</li>
</ul></div></body></html>"""
