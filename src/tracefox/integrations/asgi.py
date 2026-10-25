from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from ..utils import callable_name, format_response_body, headers_to_dict, parse_body

PAYLOAD_CAPTURE_LIMIT = 1024 * 1024


def default_asgi_user(scope: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    user = scope.get("user")
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    ident = getattr(user, "id", None) or getattr(user, "identity", None) or getattr(user, "username", None)
    return {
        "id": ident,
        "name": getattr(user, "display_name", None) or getattr(user, "name", None) or getattr(user, "username", None),
        "email": getattr(user, "email", None),
    }


class TracefoxMiddleware:
    """Pure ASGI middleware recording every HTTP request (FastAPI, Starlette, Quart, Litestar, ...)."""

    def __init__(self, app: Any, tracefox: Optional[Any] = None, user_resolver: Optional[Callable] = None):
        if tracefox is None:
            from ..core import tracefox as default

            tracefox = default
        self.app = app
        self.tracefox = tracefox
        self.user_resolver = user_resolver

    async def __call__(self, scope, receive, send) -> None:
        tracefox = self.tracefox
        if scope["type"] != "http" or not tracefox.config.enabled:
            await self.app(scope, receive, send)
            return
        watcher = tracefox.watcher("requests")
        root_path = scope.get("root_path", "") or ""
        path = scope.get("path", "/")
        route_path = path[len(root_path):] if root_path and path.startswith(root_path) else path
        if watcher is None or watcher.should_ignore(route_path or "/", scope.get("method", "GET")) or not tracefox.should_record():
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        request_body = bytearray()
        request_truncated = False
        response: Dict[str, Any] = {"status": None, "headers": {}, "body": bytearray(), "truncated": False, "recorded": False}
        limit = watcher.size_limit * 1024

        async def receive_wrapper():
            nonlocal request_truncated
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                if len(request_body) + len(chunk) <= PAYLOAD_CAPTURE_LIMIT:
                    request_body.extend(chunk)
                else:
                    request_truncated = True
            return message

        def record():
            if response["recorded"]:
                return
            response["recorded"] = True
            self._record(watcher, scope, route_path, bytes(request_body), request_truncated, response,
                         (time.perf_counter() - started) * 1000)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                response["status"] = message["status"]
                response["headers"] = headers_to_dict(message.get("headers", []))
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                if len(response["body"]) + len(chunk) <= limit:
                    response["body"].extend(chunk)
                else:
                    response["truncated"] = True
                if not message.get("more_body"):
                    record()
            await send(message)

        with tracefox.batch():
            try:
                await self.app(scope, receive_wrapper, send_wrapper)
            except Exception as exc:
                exceptions = tracefox.watcher("exceptions")
                if exceptions is not None:
                    exceptions.record_exception(exc)
                if response["status"] is None:
                    response["status"] = 500
                    response["body"] = bytearray(b"Internal Server Error")
                    response["headers"] = {"content-type": "text/plain"}
                raise
            finally:
                record()

    def _record(self, watcher, scope, route_path, body, truncated, response, duration) -> None:
        try:
            headers = headers_to_dict(scope.get("headers", []))
            query = scope.get("query_string", b"").decode("latin-1")
            if truncated:
                payload: Any = "Purged By Tracefox"
            else:
                payload = parse_body(body, headers.get("content-type"))
            if query:
                from urllib.parse import parse_qsl

                params = dict(parse_qsl(query, keep_blank_values=True))
                payload = {**params, **payload} if isinstance(payload, dict) else payload or params
            status = response["status"] or 500
            rheaders = response["headers"]
            endpoint = scope.get("endpoint")
            route = scope.get("route")
            app = scope.get("app")
            middleware = []
            for item in getattr(app, "user_middleware", []) or []:
                cls = getattr(item, "cls", item)
                middleware.append(getattr(cls, "__name__", str(cls)))
            client = scope.get("client") or (None, None)
            user = None
            resolver = self.user_resolver or self.tracefox.config.user_resolver
            try:
                if resolver is not None:
                    from starlette.requests import Request

                    user = resolver(Request(scope))
                else:
                    user = default_asgi_user(scope)
            except Exception:
                user = None
            action = callable_name(endpoint) if endpoint is not None else None
            if action and route is not None and getattr(route, "path", None):
                action = f"{action} ({route.path})"
            watcher.record_request(
                method=scope.get("method", "GET"),
                uri=(route_path or "/") + (f"?{query}" if query else ""),
                ip_address=client[0] if client else None,
                headers=headers,
                payload=payload,
                session=dict(scope["session"]) if isinstance(scope.get("session"), dict) else None,
                response_status=status,
                response_headers=rheaders,
                response=format_response_body(
                    bytes(response["body"]), rheaders.get("content-type"), status, rheaders, watcher.size_limit,
                    truncated=response["truncated"],
                ),
                duration=duration,
                controller_action=action,
                middleware=middleware,
                user=user,
            )
        except Exception:
            import logging

            logging.getLogger("tracefox").debug("Tracefox failed to record request", exc_info=True)
