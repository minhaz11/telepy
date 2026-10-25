"""FastAPI / Starlette integration.

    from telepy.integrations.fastapi import init_telepy
    init_telepy(app)                      # dashboard at /telepy
"""

from __future__ import annotations

from typing import Any, Optional

from ..core import Telepy, telepy as default_telepy
from ..dashboard.asgi import TelepyASGI
from .asgi import TelepyMiddleware


def init_telepy(app: Any, telepy: Optional[Telepy] = None, **options: Any) -> Telepy:
    telepy = telepy or default_telepy
    if "environment" not in options and telepy.config.environment is None:
        options["environment"] = "local" if getattr(app, "debug", False) else "production"
    telepy.start(**options)
    if not telepy.config.enabled:
        return telepy

    from starlette.routing import Mount

    app.add_middleware(TelepyMiddleware, telepy=telepy)
    # First in the route table so catch-all routes can't shadow the dashboard.
    app.router.routes.insert(0, Mount(telepy.config.path, app=TelepyASGI(telepy), name="telepy"))
    return telepy


init_app = init_telepy
