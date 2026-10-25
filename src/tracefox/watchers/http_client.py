from __future__ import annotations

import time
from typing import Any, Dict, Optional

from ..entry import EntryType, IncomingEntry
from ..utils import format_response_body, headers_to_dict, hide, parse_body
from .base import Watcher, safely


class HttpClientWatcher(Watcher):
    """Outgoing HTTP requests made with httpx, requests or aiohttp."""

    name = "http_client"

    def register(self) -> None:
        self.when_imported("httpx", self._register_httpx)
        self.when_imported("requests", self._register_requests)
        self.when_imported("aiohttp", self._register_aiohttp)

    @property
    def size_limit(self) -> int:
        return int(self.options.get("size_limit", 64))

    @safely
    def record_client_request(
        self,
        method: str,
        uri: str,
        headers: Dict[str, str],
        body: Optional[bytes],
        status: Optional[int],
        response_headers: Dict[str, str],
        response_body: Any,
        duration_ms: float,
        error: Optional[BaseException] = None,
    ) -> None:
        if not self.active:
            return
        config = self.tracefox.config
        content_type = headers.get("content-type")
        payload = parse_body(body, content_type) if isinstance(body, (bytes, bytearray)) else (body or {})
        if isinstance(response_body, (bytes, bytearray)):
            response = format_response_body(
                bytes(response_body), response_headers.get("content-type"), status or 0, response_headers, self.size_limit
            )
        else:
            response = response_body
        if error is not None:
            response = f"{type(error).__name__}: {error}"
        entry = IncomingEntry(EntryType.CLIENT_REQUEST, {
            "method": method.upper(),
            "uri": uri,
            "headers": hide(headers, config.hidden_request_headers),
            "payload": hide(payload, config.hidden_request_parameters),
            "response_status": status,
            "response_headers": response_headers,
            "response": hide(response, config.hidden_response_parameters),
            "duration": int(round(duration_ms)),
        })
        if error is not None:
            entry.add_tags(["failed"])
        self.tracefox.record(entry)

    # -- httpx -----------------------------------------------------------------------

    def _register_httpx(self, httpx: Any) -> None:
        watcher = self

        def request_parts(request):
            try:
                body = request.content
            except Exception:
                body = None
            return request.method, str(request.url), headers_to_dict(request.headers.raw), body

        def response_parts(response, stream):
            body: Any = "Streamed Response" if stream else None
            if not stream:
                try:
                    body = response.content
                except Exception:
                    body = "Streamed Response"
            return response.status_code, headers_to_dict(response.headers.raw), body

        def make_sync(original):
            def send(self, request, *args, **kwargs):
                started = time.perf_counter()
                method, uri, headers, body = request_parts(request)
                try:
                    response = original(self, request, *args, **kwargs)
                except Exception as exc:
                    watcher.record_client_request(method, uri, headers, body, None, {}, None, (time.perf_counter() - started) * 1000, exc)
                    raise
                status, rheaders, rbody = response_parts(response, kwargs.get("stream", False))
                watcher.record_client_request(method, uri, headers, body, status, rheaders, rbody, (time.perf_counter() - started) * 1000)
                return response
            return send

        def make_async(original):
            async def send(self, request, *args, **kwargs):
                started = time.perf_counter()
                method, uri, headers, body = request_parts(request)
                try:
                    response = await original(self, request, *args, **kwargs)
                except Exception as exc:
                    watcher.record_client_request(method, uri, headers, body, None, {}, None, (time.perf_counter() - started) * 1000, exc)
                    raise
                status, rheaders, rbody = response_parts(response, kwargs.get("stream", False))
                watcher.record_client_request(method, uri, headers, body, status, rheaders, rbody, (time.perf_counter() - started) * 1000)
                return response
            return send

        self.patch(httpx.Client, "send", make_sync)
        self.patch(httpx.AsyncClient, "send", make_async)

    # -- requests --------------------------------------------------------------------

    def _register_requests(self, requests: Any) -> None:
        watcher = self

        def make(original):
            def send(self, request, **kwargs):
                started = time.perf_counter()
                body = request.body.encode() if isinstance(request.body, str) else request.body
                headers = {k.lower(): v for k, v in request.headers.items()}
                try:
                    response = original(self, request, **kwargs)
                except Exception as exc:
                    watcher.record_client_request(request.method, request.url, headers, body, None, {}, None, (time.perf_counter() - started) * 1000, exc)
                    raise
                rbody = "Streamed Response" if kwargs.get("stream") else response.content
                watcher.record_client_request(
                    request.method, request.url, headers, body, response.status_code,
                    {k.lower(): v for k, v in response.headers.items()}, rbody, (time.perf_counter() - started) * 1000,
                )
                return response
            return send

        self.patch(requests.Session, "send", make)

    # -- aiohttp ---------------------------------------------------------------------

    def _register_aiohttp(self, aiohttp: Any) -> None:
        watcher = self
        limit = self.size_limit * 1024

        def make(original):
            async def _request(self, method, str_or_url, **kwargs):
                started = time.perf_counter()
                data = kwargs.get("data")
                body = kwargs.get("json") if kwargs.get("json") is not None else data
                headers = {k.lower(): v for k, v in (kwargs.get("headers") or {}).items()}
                try:
                    response = await original(self, method, str_or_url, **kwargs)
                except Exception as exc:
                    watcher.record_client_request(method, str(str_or_url), headers, None, None, {}, None, (time.perf_counter() - started) * 1000, exc)
                    raise
                rbody: Any = "Streamed Response"
                if response.content_length is not None and response.content_length <= limit:
                    try:
                        rbody = await response.read()
                    except Exception:
                        pass
                watcher.record_client_request(
                    method, str(response.url), headers, body if isinstance(body, (bytes, bytearray, dict, list)) else None,
                    response.status, {k.lower(): v for k, v in response.headers.items()}, rbody,
                    (time.perf_counter() - started) * 1000,
                )
                return response
            return _request

        self.patch(aiohttp.ClientSession, "_request", make)
