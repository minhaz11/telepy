from __future__ import annotations

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from ...core import telepy
from ...dashboard.api import Dashboard, DashboardRequest

_dashboard = Dashboard(telepy)


@csrf_exempt
def dashboard(request, subpath: str = ""):
    base = request.path[: len(request.path) - len(subpath)].rstrip("/")
    result = _dashboard.handle(DashboardRequest(
        method=request.method,
        path="/" + subpath,
        query=request.GET.dict(),
        body=request.body,
        headers={k.lower(): v for k, v in request.headers.items()},
        base_path=base,
        native=request,
    ))
    response = HttpResponse(result.body, status=result.status)
    for key, value in result.headers:
        response[key] = value
    return response
