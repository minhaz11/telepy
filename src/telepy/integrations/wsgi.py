from __future__ import annotations

import io
import logging
import time
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qsl

from ..dashboard.wsgi import environ_headers
from ..utils import format_response_body, headers_to_dict, parse_body

PAYLOAD_CAPTURE_LIMIT = 1024 * 1024
logger = logging.getLogger("telepy")


class TelepyWSGIMiddleware:
    """WSGI middleware recording every HTTP request (Flask, Bottle, Falcon, Pyramid, ...).

    Frameworks can enrich the entry by setting ``environ["telepy.user"]``, ``environ["telepy.session"]``,
    ``environ["telepy.controller_action"]``, ``environ["telepy.middleware"]`` or ``environ["telepy.payload"]``.
    """

    def __init__(self, app: Any, telepy: Optional[Any] = None, user_resolver: Optional[Callable] = None):
        if telepy is None:
            from ..core import telepy as default

            telepy = default
        self.app = app
        self.telepy = telepy
        self.user_resolver = user_resolver

    def __call__(self, environ, start_response):
        telepy = self.telepy
        watcher = telepy.watcher("requests") if telepy.config.enabled else None
        path = environ.get("PATH_INFO", "/") or "/"
        method = environ.get("REQUEST_METHOD", "GET")
        if watcher is None or watcher.should_ignore(path, method) or not telepy.should_record():
            return self.app(environ, start_response)

        started = time.perf_counter()
        body = b""
        truncated = False
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        if 0 < length <= PAYLOAD_CAPTURE_LIMIT:
            body = environ["wsgi.input"].read(length)
            environ["wsgi.input"] = io.BytesIO(body)
        elif length > PAYLOAD_CAPTURE_LIMIT:
            truncated = True

        state: Dict[str, Any] = {"status": 500, "headers": {}, "body": bytearray(), "truncated": False}
        limit = watcher.size_limit * 1024

        def start_response_wrapper(status, headers, exc_info=None):
            state["status"] = int(str(status).split(" ", 1)[0])
            state["headers"] = headers_to_dict(headers)
            return start_response(status, headers, exc_info)

        batch, token = telepy.start_batch()

        def finish():
            try:
                self._record(watcher, environ, body, truncated, state, (time.perf_counter() - started) * 1000)
            finally:
                telepy.end_batch(batch, token)

        try:
            result = self.app(environ, start_response_wrapper)
        except Exception as exc:
            exceptions = telepy.watcher("exceptions")
            if exceptions is not None:
                exceptions.record_exception(exc)
            state["status"] = 500
            finish()
            raise
        return _ClosingIterator(result, state, limit, finish)

    def _record(self, watcher, environ, body, truncated, state, duration) -> None:
        try:
            headers = environ_headers(environ)
            query = environ.get("QUERY_STRING", "")
            if "telepy.payload" in environ:
                payload: Any = environ["telepy.payload"]
            elif truncated:
                payload = "Purged By Telepy"
            else:
                payload = parse_body(body, headers.get("content-type"))
                if query:
                    params = dict(parse_qsl(query, keep_blank_values=True))
                    payload = {**params, **payload} if isinstance(payload, dict) else payload or params
            user = environ.get("telepy.user")
            resolver = self.user_resolver or self.telepy.config.user_resolver
            if user is None and resolver is not None:
                try:
                    user = resolver(environ)
                except Exception:
                    user = None
            status, rheaders = state["status"], state["headers"]
            path = environ.get("SCRIPT_NAME", "") + (environ.get("PATH_INFO", "/") or "/")
            watcher.record_request(
                method=environ.get("REQUEST_METHOD", "GET"),
                uri=path + (f"?{query}" if query else ""),
                ip_address=environ.get("REMOTE_ADDR"),
                headers=headers,
                payload=payload,
                session=environ.get("telepy.session"),
                response_status=status,
                response_headers=rheaders,
                response=format_response_body(
                    bytes(state["body"]), rheaders.get("content-type"), status, rheaders, watcher.size_limit,
                    truncated=state["truncated"],
                ),
                duration=duration,
                controller_action=environ.get("telepy.controller_action"),
                middleware=environ.get("telepy.middleware"),
                user=user,
            )
        except Exception:
            logger.debug("Telepy failed to record request", exc_info=True)


class _ClosingIterator:
    def __init__(self, result, state, limit, finish):
        self._result = result
        self._iterator = iter(result)
        self._state = state
        self._limit = limit
        self._finish = finish
        self._done = False

    def __iter__(self):
        return self

    def __next__(self):
        try:
            chunk = next(self._iterator)
        except StopIteration:
            self._complete()
            raise
        buffer = self._state["body"]
        if len(buffer) + len(chunk) <= self._limit:
            buffer.extend(chunk)
        else:
            self._state["truncated"] = True
        return chunk

    def close(self):
        try:
            close = getattr(self._result, "close", None)
            if close is not None:
                close()
        finally:
            self._complete()

    def _complete(self):
        if not self._done:
            self._done = True
            self._finish()
