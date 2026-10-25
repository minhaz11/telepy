from __future__ import annotations

import os

from django.apps import AppConfig


class PyTelepyConfig(AppConfig):
    name = "telepy.integrations.django"
    label = "telepy"
    verbose_name = "Telepy"

    def ready(self) -> None:
        from django.conf import settings
        from django.core.signals import got_request_exception

        from ...core import telepy

        options = {k.lower(): v for k, v in (getattr(settings, "TELEPY", None) or {}).items()}
        if "environment" not in options and telepy.config.environment is None:
            options["environment"] = "local" if settings.DEBUG else "production"
        if "database_url" not in options and not os.getenv("TELEPY_DATABASE_URL"):
            base_dir = getattr(settings, "BASE_DIR", None)
            if base_dir:
                options["database_url"] = f"sqlite:///{os.path.join(str(base_dir), 'telepy.sqlite3')}"
        telepy.start(**options)

        def on_exception(sender, request=None, **kwargs):
            import sys

            exc = sys.exc_info()[1]
            exceptions = telepy.watcher("exceptions")
            if exc is not None and exceptions is not None:
                exceptions.record_exception(exc)

        got_request_exception.connect(on_exception, weak=False, dispatch_uid="telepy_exceptions")
