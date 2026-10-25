from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field, fields
from typing import Any, Callable, Dict, List, Optional


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_environment() -> Optional[str]:
    for name in ("TRACEFOX_ENV", "APP_ENV", "ENVIRONMENT", "ENV"):
        if os.getenv(name):
            return os.environ[name].strip().lower()
    return None


DEFAULT_WATCHERS: Dict[str, Any] = {
    "batches": True,
    "cache": {"hidden": []},
    "commands": {"ignore": [
        "runserver", "run", "shell", "dbshell", "celery", "worker", "beat", "uvicorn",
        "gunicorn", "dev", "tracefox", "tracefox_prune", "tracefox_clear", "migrate",
        "makemigrations", "collectstatic", "showmigrations", "check", "test",
    ]},
    "dumps": True,
    "events": {"ignore": [], "include_framework": False},
    "exceptions": {"ignore": []},
    "gates": {"ignore_abilities": []},
    "http_client": {"size_limit": 64},
    "jobs": True,
    "logs": {"level": "DEBUG", "ignore_loggers": [
        "uvicorn", "uvicorn.access", "uvicorn.error", "werkzeug", "sqlalchemy", "tracefox",
        "django.db.backends", "django.server", "django.utils.autoreload", "httpx", "httpcore",
        "urllib3", "asyncio", "multipart", "python_multipart", "watchfiles", "celery", "kombu",
        "amqp", "apscheduler", "faker", "parso", "PIL",
    ]},
    "mail": True,
    "models": {"events": ["created", "updated", "deleted"], "hydrations": True},
    "notifications": True,
    "queries": {"slow": 100, "ignore_patterns": []},
    "redis": True,
    "requests": {"size_limit": 64, "ignore_http_methods": [], "ignore_status_codes": []},
    "schedule": True,
    "views": True,
}


@dataclass
class TracefoxConfig:
    enabled: bool = field(default_factory=lambda: _env_bool("TRACEFOX_ENABLED", True))
    path: str = field(default_factory=lambda: os.getenv("TRACEFOX_PATH", "/tracefox"))
    database_url: str = field(
        default_factory=lambda: os.getenv("TRACEFOX_DATABASE_URL", "sqlite:///tracefox.sqlite3")
    )
    # "local" records everything and opens the dashboard to everyone; anything else
    # applies the default filter and requires `authorization` to return True.
    environment: Optional[str] = field(default_factory=_env_environment)
    authorization: Optional[Callable[[Any], bool]] = None
    watchers: Dict[str, Any] = field(default_factory=dict)

    ignore_paths: List[str] = field(default_factory=lambda: [
        "/favicon.ico", "/static/*", "/_next/*", "/__debug__/*", "/livewire*",
    ])
    only_paths: List[str] = field(default_factory=list)
    hidden_request_headers: List[str] = field(default_factory=lambda: [
        "authorization", "php-auth-pw", "cookie", "x-csrftoken", "x-csrf-token", "x-xsrf-token",
        "x-api-key", "proxy-authorization",
    ])
    hidden_request_parameters: List[str] = field(default_factory=lambda: [
        "password", "password_confirmation", "password1", "password2", "new_password",
        "old_password", "current_password", "csrfmiddlewaretoken", "token", "secret",
    ])
    hidden_response_parameters: List[str] = field(default_factory=list)

    async_writes: bool = True
    auto_migrate: bool = True
    auto_prune_hours: Optional[int] = None
    default_filter: bool = True
    user_resolver: Optional[Callable[[Any], Optional[Dict[str, Any]]]] = None
    hostname: Optional[str] = None
    database_engine_options: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        merged = copy.deepcopy(DEFAULT_WATCHERS)
        for name, value in (self.watchers or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(name), dict):
                merged[name] = {**merged[name], **value}
            elif isinstance(value, dict):
                merged[name] = dict(value)
            else:
                merged[name] = value
        self.watchers = merged
        self.path = "/" + self.path.strip("/")

    @property
    def is_local(self) -> bool:
        return (self.environment or "production") in ("local", "dev", "development", "debug")

    def watcher_enabled(self, name: str) -> bool:
        value = self.watchers.get(name, False)
        if isinstance(value, dict):
            return bool(value.get("enabled", True))
        return bool(value)

    def watcher_options(self, name: str) -> Dict[str, Any]:
        value = self.watchers.get(name)
        return dict(value) if isinstance(value, dict) else {}

    def update(self, **options: Any) -> None:
        names = {f.name for f in fields(self)}
        for key, value in options.items():
            if key not in names:
                raise TypeError(f"Unknown Tracefox option: {key}")
            if key == "watchers":
                incoming = TracefoxConfig(watchers=value).watchers
                for name, val in (value or {}).items():
                    self.watchers[name] = incoming[name]
                continue
            setattr(self, key, value)
        self.path = "/" + self.path.strip("/")
