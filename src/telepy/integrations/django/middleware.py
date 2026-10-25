from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

from ...core import telepy
from ...utils import format_response_body, to_jsonable

try:
    from asgiref.sync import iscoroutinefunction, markcoroutinefunction
except ImportError:  # asgiref < 3.6
    from asyncio import iscoroutinefunction  # type: ignore

    def markcoroutinefunction(func):  # type: ignore
        import asyncio

        func._is_coroutine = asyncio.coroutines._is_coroutine
        return func

logger = logging.getLogger("telepy")


def django_user(request) -> Optional[Dict[str, Any]]:
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return {
        "id": user.pk,
        "name": user.get_full_name() if hasattr(user, "get_full_name") and user.get_full_name() else user.get_username(),
        "email": getattr(user, "email", None),
    }


class TelepyMiddleware:
    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        self.is_async = iscoroutinefunction(get_response)
        if self.is_async:
            markcoroutinefunction(self)

    def _watcher(self, request):
        if not telepy.config.enabled:
            return None
        watcher = telepy.watcher("requests")
        if watcher is None or watcher.should_ignore(request.path_info, request.method) or not telepy.should_record():
            return None
        return watcher

    def __call__(self, request):
        if self.is_async:
            return self.__acall__(request)
        watcher = self._watcher(request)
        if watcher is None:
            return self.get_response(request)
        with telepy.batch() as batch:
            started = time.perf_counter()
            response = self.get_response(request)
            self._record(watcher, batch, request, response, (time.perf_counter() - started) * 1000)
        return response

    async def __acall__(self, request):
        watcher = self._watcher(request)
        if watcher is None:
            return await self.get_response(request)
        with telepy.batch() as batch:
            started = time.perf_counter()
            response = await self.get_response(request)
            with telepy.without_recording():
                from asgiref.sync import sync_to_async

                context = await sync_to_async(self._collect, thread_sensitive=True)(request)
            self._record(watcher, batch, request, response, (time.perf_counter() - started) * 1000, context)
        return response

    def _collect(self, request) -> Dict[str, Any]:
        """Bits that may hit the database (session, user) — gathered with recording suspended."""
        with telepy.without_recording():
            session = None
            store = getattr(request, "session", None)
            if store is not None and getattr(store, "_session_cache", None) is not None:
                session = to_jsonable(dict(store.items()), depth=6)
            resolver = telepy.config.user_resolver
            try:
                user = resolver(request) if resolver else django_user(request)
            except Exception:
                user = None
            return {"session": session, "user": user}

    def _payload(self, request) -> Any:
        content_type = (request.content_type or "").lower()
        payload: Any = {}
        try:
            if "json" in content_type:
                payload = json.loads(request.body or b"{}")
            elif request.method not in ("GET", "HEAD") and ("form" in content_type):
                payload = {k: (v if len(v) > 1 else v[0]) for k, v in request.POST.lists()}
                for name, file in request.FILES.items():
                    payload[name] = {"filename": file.name, "size": file.size, "type": file.content_type}
            elif request.body:
                payload = request.body.decode("utf-8", "replace")
        except Exception:
            payload = {}
        if request.GET:
            params = {k: (v if len(v) > 1 else v[0]) for k, v in request.GET.lists()}
            payload = {**params, **payload} if isinstance(payload, dict) else payload
        return payload

    def _record(self, watcher, batch, request, response, duration, context=None) -> None:
        try:
            match = getattr(request, "resolver_match", None)
            if match is not None and match.app_name == "telepy":
                batch.entries.clear()
                return
            if context is None:
                context = self._collect(request)
            from django.conf import settings

            headers = {k.lower(): v for k, v in request.headers.items()}
            rheaders = {k.lower(): v for k, v in (response.headers.items() if hasattr(response, "headers") else response.items())}
            if getattr(response, "streaming", False):
                body: Any = "Streamed Response"
            else:
                body = format_response_body(
                    response.content, rheaders.get("content-type"), response.status_code, rheaders, watcher.size_limit,
                )
            action = None
            if match is not None:
                action = getattr(match, "_func_path", None) or match.view_name
                if getattr(match, "route", None):
                    action = f"{action} ({match.route})"
            watcher.record_request(
                method=request.method,
                uri=request.get_full_path(),
                ip_address=request.META.get("REMOTE_ADDR"),
                headers=headers,
                payload=self._payload(request),
                session=context.get("session"),
                response_status=response.status_code,
                response_headers=rheaders,
                response=body,
                duration=duration,
                controller_action=action,
                middleware=[m.rsplit(".", 1)[-1] for m in getattr(settings, "MIDDLEWARE", [])],
                user=context.get("user"),
            )
        except Exception:
            logger.debug("Telepy failed to record request", exc_info=True)
