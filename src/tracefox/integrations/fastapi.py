"""FastAPI / Starlette integration.

    from tracefox.integrations.fastapi import init_tracefox
    init_tracefox(app)                      # dashboard at /tracefox
"""

from __future__ import annotations

from typing import Any, Optional

from ..core import Tracefox, tracefox as default_tracefox
from ..dashboard.asgi import TracefoxASGI
from .asgi import TracefoxMiddleware


def init_tracefox(app: Any, tracefox: Optional[Tracefox] = None, **options: Any) -> Tracefox:
    tracefox = tracefox or default_tracefox
    if "environment" not in options and tracefox.config.environment is None:
        options["environment"] = "local" if getattr(app, "debug", False) else "production"
    tracefox.start(**options)
    if not tracefox.config.enabled:
        return tracefox

    from starlette.routing import Mount

    app.add_middleware(TracefoxMiddleware, tracefox=tracefox)
    # First in the route table so catch-all routes can't shadow the dashboard.
    app.router.routes.insert(0, Mount(tracefox.config.path, app=TracefoxASGI(tracefox), name="tracefox"))
    return tracefox


init_app = init_tracefox
