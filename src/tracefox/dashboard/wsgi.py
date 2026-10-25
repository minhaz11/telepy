from __future__ import annotations

from http import HTTPStatus
from typing import Any, Optional
from urllib.parse import parse_qsl

from .api import Dashboard, DashboardRequest


def environ_headers(environ) -> dict:
    headers = {k[5:].replace("_", "-").lower(): v for k, v in environ.items() if k.startswith("HTTP_")}
    if environ.get("CONTENT_TYPE"):
        headers["content-type"] = environ["CONTENT_TYPE"]
    if environ.get("CONTENT_LENGTH"):
        headers["content-length"] = environ["CONTENT_LENGTH"]
    return headers


class TracefoxWSGI:
    """The dashboard as a WSGI app, e.g. ``DispatcherMiddleware(app, {"/tracefox": TracefoxWSGI()})``."""

    def __init__(self, tracefox: Optional[Any] = None):
        if tracefox is None:
            from ..core import tracefox as default

            tracefox = default
        self.tracefox = tracefox
        self.dashboard = Dashboard(tracefox)

    def __call__(self, environ, start_response):
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        body = environ["wsgi.input"].read(length) if length else b""
        request = DashboardRequest(
            method=environ.get("REQUEST_METHOD", "GET"),
            path=environ.get("PATH_INFO", "/") or "/",
            query=dict(parse_qsl(environ.get("QUERY_STRING", ""))),
            body=body,
            headers=environ_headers(environ),
            base_path=environ.get("SCRIPT_NAME", "") or self.tracefox.config.path,
            native=environ,
        )
        response = self.dashboard.handle(request)
        status = f"{response.status} {HTTPStatus(response.status).phrase if response.status in HTTPStatus._value2member_map_ else ''}"
        start_response(status.strip(), response.headers + [("content-length", str(len(response.body)))])
        return [response.body]
