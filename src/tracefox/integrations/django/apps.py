from __future__ import annotations

import os

from django.apps import AppConfig


class TracefoxAppConfig(AppConfig):
    name = "tracefox.integrations.django"
    label = "tracefox"
    verbose_name = "TraceFox"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from django.conf import settings
        from django.core.signals import got_request_exception

        from ...core import tracefox

        options = {k.lower(): v for k, v in (getattr(settings, "TRACEFOX", None) or {}).items()}
        if "environment" not in options and tracefox.config.environment is None:
            options["environment"] = "local" if settings.DEBUG else "production"
        connection = options.pop("connection", "default")
        if "database_url" not in options and "engine" not in options and not os.getenv("TRACEFOX_DATABASE_URL"):
            # Like Laravel Telescope: entries live in the project's own database (tables from `manage.py migrate`).
            from .storage import DjangoStorage

            tracefox.storage = DjangoStorage(using=connection)
        tracefox.start(**options)

        def on_exception(sender, request=None, **kwargs):
            import sys

            exc = sys.exc_info()[1]
            exceptions = tracefox.watcher("exceptions")
            if exc is not None and exceptions is not None:
                exceptions.record_exception(exc)

        got_request_exception.connect(on_exception, weak=False, dispatch_uid="tracefox_exceptions")
