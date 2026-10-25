from __future__ import annotations

import asyncio
import functools
from typing import Any, Optional
from urllib.parse import parse_qsl

from ..utils import headers_to_dict
from .api import Dashboard, DashboardRequest


class TelepyASGI:
    """The dashboard as an ASGI app: ``app.mount("/telepy", TelepyASGI())``."""

    def __init__(self, telepy: Optional[Any] = None):
        if telepy is None:
            from ..core import telepy as default

            telepy = default
        self.telepy = telepy
        self.dashboard = Dashboard(telepy)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        root_path = scope.get("root_path", "") or ""
        path = scope.get("path", "/")
        if root_path and path.startswith(root_path):
            path = path[len(root_path):]
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        native = None
        try:
            from starlette.requests import Request

            native = Request(scope)
        except Exception:
            native = scope
        request = DashboardRequest(
            method=scope["method"],
            path=path or "/",
            query=dict(parse_qsl(scope.get("query_string", b"").decode("latin-1"))),
            body=body,
            headers=headers_to_dict(scope.get("headers", [])),
            base_path=root_path or self.telepy.config.path,
            native=native,
        )
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, functools.partial(self.dashboard.handle, request))
        await send({
            "type": "http.response.start",
            "status": response.status,
            "headers": [(k.encode("latin-1"), v.encode("latin-1")) for k, v in response.headers]
            + [(b"content-length", str(len(response.body)).encode())],
        })
        await send({"type": "http.response.body", "body": b"" if scope["method"] == "HEAD" else response.body})
