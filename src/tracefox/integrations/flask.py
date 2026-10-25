"""Flask integration.

    from tracefox.integrations.flask import FlaskTracefox
    FlaskTracefox(app)                      # dashboard at /tracefox, `flask tracefox ...` commands
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..core import Tracefox, tracefox as default_tracefox
from ..dashboard.api import Dashboard, DashboardRequest
from ..utils import callable_name, to_jsonable
from .wsgi import TracefoxWSGIMiddleware


def _flask_user() -> Optional[Dict[str, Any]]:
    try:
        from flask_login import current_user  # type: ignore

        if current_user and current_user.is_authenticated:
            return {
                "id": getattr(current_user, "id", None) or current_user.get_id(),
                "name": getattr(current_user, "name", None) or getattr(current_user, "username", None),
                "email": getattr(current_user, "email", None),
            }
    except Exception:
        pass
    try:
        from flask import g

        user = g.get("user")
        if user is not None:
            if isinstance(user, dict):
                return user
            return {"id": getattr(user, "id", None), "name": getattr(user, "name", None), "email": getattr(user, "email", None)}
    except Exception:
        pass
    return None


class FlaskTracefox:
    def __init__(self, app: Any = None, tracefox: Optional[Tracefox] = None, **options: Any):
        self.tracefox = tracefox or default_tracefox
        self.options = options
        if app is not None:
            self.init_app(app)

    def init_app(self, app: Any) -> None:
        from flask import Blueprint, Response, got_request_exception, request, session

        tracefox = self.tracefox
        options = {**{k.lower(): v for k, v in (app.config.get("TRACEFOX") or {}).items()}, **self.options}
        if "environment" not in options and tracefox.config.environment is None:
            options["environment"] = "local" if app.debug else "production"
        tracefox.start(**options)
        app.extensions["tracefox"] = self
        if not tracefox.config.enabled:
            return

        app.wsgi_app = TracefoxWSGIMiddleware(app.wsgi_app, tracefox)

        def on_exception(sender, exception, **_):
            exceptions = tracefox.watcher("exceptions")
            if exceptions is not None:
                exceptions.record_exception(exception)

        got_request_exception.connect(on_exception, app, weak=False)

        @app.after_request
        def _tracefox_enrich(response):
            try:
                environ = request.environ
                endpoint = app.view_functions.get(request.endpoint) if request.endpoint else None
                if endpoint is not None:
                    rule = request.url_rule.rule if request.url_rule else ""
                    environ["tracefox.controller_action"] = f"{callable_name(endpoint)} ({rule})"
                environ["tracefox.session"] = to_jsonable(dict(session), depth=6) if session else {}
                resolver = tracefox.config.user_resolver
                environ["tracefox.user"] = resolver(request) if resolver else _flask_user()
                payload: Any = request.get_json(silent=True)
                if payload is None and (request.form or request.files):
                    payload = request.form.to_dict(flat=True)
                    for name, file in request.files.items():
                        payload[name] = {"filename": file.filename, "type": file.mimetype}
                if payload is not None:
                    if request.args and isinstance(payload, dict):
                        payload = {**request.args.to_dict(flat=True), **payload}
                    environ["tracefox.payload"] = payload
            except Exception:
                pass
            return response

        dashboard = Dashboard(tracefox)
        blueprint = Blueprint("tracefox", __name__)
        base = tracefox.config.path.rstrip("/")

        def view(subpath: str = ""):
            dashboard_request = DashboardRequest(
                method=request.method,
                path="/" + subpath,
                query=request.args.to_dict(flat=True),
                body=request.get_data(),
                headers={k.lower(): v for k, v in request.headers.items()},
                base_path=request.script_root + base,
                native=request,
            )
            result = dashboard.handle(dashboard_request)
            return Response(result.body, status=result.status, headers=result.headers)

        methods = ["GET", "POST", "DELETE", "HEAD"]
        blueprint.add_url_rule(base, "index", view, methods=methods, strict_slashes=False)
        blueprint.add_url_rule(f"{base}/<path:subpath>", "path", view, methods=methods)
        app.register_blueprint(blueprint)
        self._register_cli(app)

    def _register_cli(self, app: Any) -> None:
        import click
        from flask.cli import AppGroup

        tracefox = self.tracefox
        group = AppGroup("tracefox", help="Manage Tracefox entries.")

        @group.command("prune")
        @click.option("--hours", default=24, show_default=True, help="Delete entries older than this many hours.")
        @click.option("--keep-exceptions", is_flag=True, help="Keep exception entries.")
        def prune(hours, keep_exceptions):
            click.echo(f"{tracefox.prune(hours, keep_exceptions)} entries pruned.")

        @group.command("clear")
        def clear():
            tracefox.clear()
            click.echo("Tracefox entries cleared!")

        @group.command("pause")
        def pause():
            tracefox.pause_recording()
            click.echo("Tracefox recording paused.")

        @group.command("resume")
        def resume():
            tracefox.resume_recording()
            click.echo("Tracefox recording resumed.")

        app.cli.add_command(group)


init_app = FlaskTracefox
