# tracefox

[![PyPI](https://img.shields.io/pypi/v/tracefox)](https://pypi.org/project/tracefox/) [![CI](https://github.com/minhaz11/tracefox/actions/workflows/ci.yml/badge.svg)](https://github.com/minhaz11/tracefox/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

An elegant debug assistant for Python web apps, modelled on Laravel Telescope. It records the requests coming into your app, plus the exceptions, logs, database queries, ORM model changes, queued jobs, mail, notifications, cache operations, Redis commands, scheduled tasks, outgoing HTTP calls, template renders and variable dumps behind them. All of it appears in a Telescope-style dashboard.

It works with **FastAPI / Starlette**, **Flask** and **Django**, plus any other ASGI or WSGI framework.

```
┌─────────────┐  hooks   ┌──────────┐  batch   ┌────────┐  thread  ┌──────────────┐
│ your app    │ ───────► │ watchers │ ───────► │ core   │ ───────► │ SQL storage  │ ◄── dashboard (SPA)
│ (any fw)    │          │ (18)     │          │ filter │          │ (SQLAlchemy) │
└─────────────┘          └──────────┘          └────────┘          └──────────────┘
```

## Features

Everything that Laravel Telescope does, mapped to the Python ecosystem:

| Telescope screen | What tracefox records | Hooks |
|---|---|---|
| **Requests** | method, path, controller action, middleware, headers, payload, session, response, status, duration, memory, authenticated user | ASGI middleware, WSGI middleware, Django middleware |
| **Commands** | command, arguments, options, exit code (plus every entry recorded while it ran) | Click / Typer / Flask CLI, Django management commands, `tracefox.command()` |
| **Schedule** | command, expression, description, timezone, output | APScheduler 3.x, Celery beat |
| **Jobs** | name, queue, connection, tries, timeout, data, status `pending → processed / failed`, exception | Celery (dispatch + worker), FastAPI/Starlette `BackgroundTasks`, `@tracefox.job` |
| **Batches** | total / pending / processed / failed jobs, progress | Celery `group` / `chord` |
| **Cache** | hit / missed / set / forget, key, value, expiration | Django cache framework, Flask-Caching, `tracefox.record_cache()` |
| **Dumps** | dumped values with file:line | `tracefox.dump(value)` |
| **Events** | name, payload, listeners | Django signals, blinker (Flask) signals, `tracefox.record_event()` |
| **Exceptions** | class, message, location with code preview, stack trace, context, occurrences grouped by family | unhandled request errors, `logger.exception()`, `sys.excepthook`, thread excepthook |
| **Gates** | ability, allowed / denied, arguments, location | Django `has_perm`, `@tracefox.gate()`, `tracefox.record_gate()` |
| **HTTP Client** | method, URI, headers, payload, status, response, duration | httpx (sync + async), requests, aiohttp |
| **Logs** | level, message, channel (logger), context (`extra=`), location | stdlib `logging` |
| **Mail** | mailable, from, to, cc, bcc, reply-to, subject, HTML preview, `.eml` download | Django mail backends, smtplib (Flask-Mail), aiosmtplib (fastapi-mail), `tracefox.record_mail()` |
| **Models** | created / updated (with changed attributes) / deleted, plus hydration counts per request | SQLAlchemy ORM, Django ORM |
| **Notifications** | notification, notifiable, channel, response | `tracefox.record_notification()` |
| **Queries** | SQL with bindings interpolated, bindings, duration, `slow` tag, file:line of your code | SQLAlchemy (every engine), Django ORM |
| **Redis** | command, connection, duration (pipelines too) | redis-py (sync + asyncio) |
| **Views** | template name, path, data keys | Jinja2 (Flask, Starlette templates), Django templates |

Plus the rest of Telescope's machinery:

- **Batches of related entries.** Everything recorded during one request, job or command shares a `batch_id`. The detail page shows it all in tabs: a request's queries, logs, exceptions, jobs, mail and so on.
- **Tags.** Tags are added automatically: `Auth:<id>`, model keys like `app.models.User:1`, `slow`, `failed` and mail recipients. You can add your own with `tracefox.tag(...)` and search by tag on every screen.
- **Monitoring.** Entries with a monitored tag are always recorded, even when filters would drop them.
- **Exception families.** Repeats of the same exception collapse into one row with an occurrence count and an "other occurrences" view.
- **Filtering.** Outside `local`, a default filter keeps only failed requests, exceptions, failed jobs, scheduled tasks, slow queries and monitored tags. You can add `tracefox.filter(...)` / `tracefox.filter_batch(...)`.
- **Privacy.** Sensitive request headers and parameters are masked as `********`. `password`, `authorization` and `cookie` are masked by default, and the lists are configurable.
- **Pause / resume recording, clear all entries, light / dark / system theme, live "Load New Entries", pruning.**
- **Safe by default.** The dashboard returns 403 outside `local` unless you pass an `authorization` callback. Every write needs a custom header, which blocks CSRF. Watchers never raise into your app.
- **Low overhead.** Entries are buffered per batch and written on a background thread. Libraries are hooked only if your app imports them, through import hooks.

## Install

```bash
pip install tracefox            # core (SQLAlchemy is the only dependency)
pip install "tracefox[fastapi]" # or [flask] / [django]
```

Entries are stored with SQLAlchemy, so any database it supports works. The default is a local SQLite file (`tracefox.sqlite3`). Tables are created automatically.

## Quick start

### FastAPI / Starlette

```python
from fastapi import FastAPI
from tracefox.integrations.fastapi import init_tracefox

app = FastAPI(debug=True)
init_tracefox(app)            # middleware + dashboard at /tracefox
```

### Flask

```python
from flask import Flask
from tracefox.integrations.flask import FlaskTracefox

app = Flask(__name__)
app.config["TRACEFOX"] = {"database_url": "sqlite:///tracefox.sqlite3"}
FlaskTracefox(app)            # middleware + dashboard + `flask tracefox prune|clear|pause|resume`
```

### Django

```python
# settings.py
INSTALLED_APPS += ["tracefox.integrations.django"]
MIDDLEWARE.insert(0, "tracefox.integrations.django.middleware.TracefoxMiddleware")
TRACEFOX = {
    "authorization": lambda request: request.user.is_staff,   # who may open the dashboard outside DEBUG
}

# urls.py
urlpatterns += [path("tracefox/", include("tracefox.integrations.django.urls"))]
```

Management commands: `tracefox_prune [--hours 48] [--keep-exceptions]`, `tracefox_clear`, `tracefox_pause`, `tracefox_resume`.

### Any other ASGI / WSGI framework

```python
from tracefox import tracefox
from tracefox.integrations import TracefoxMiddleware, TracefoxWSGIMiddleware
from tracefox.dashboard import TracefoxASGI, TracefoxWSGI

tracefox.start(environment="local")
asgi_app = TracefoxMiddleware(asgi_app)                 # record requests
# mount TracefoxASGI() (or TracefoxWSGI()) at /tracefox with your router
```

Open **http://localhost:8000/tracefox**.

## Configuration

Options are passed as keyword arguments to `init_tracefox(app, ...)` / `FlaskTracefox(app, ...)` / `tracefox.start(...)`, through Flask's `app.config["TRACEFOX"]`, or through Django's `TRACEFOX = {...}`.

| Option | Default | |
|---|---|---|
| `enabled` | `$TRACEFOX_ENABLED` or `True` | master switch |
| `environment` | `$TRACEFOX_ENV` / `$APP_ENV`; otherwise `local` when the framework is in debug mode, else `production` | `local` records everything and opens the dashboard |
| `path` | `/tracefox` | dashboard URL |
| `database_url` | `$TRACEFOX_DATABASE_URL` or `sqlite:///tracefox.sqlite3` | any SQLAlchemy URL |
| `authorization` | `None` | `callable(request) -> bool`; receives the framework's request object |
| `watchers` | all enabled | e.g. `{"queries": {"slow": 50}, "redis": False, "logs": {"level": "WARNING"}}` |
| `ignore_paths` / `only_paths` | static assets ignored | glob patterns for request recording |
| `hidden_request_headers` / `hidden_request_parameters` / `hidden_response_parameters` | auth headers, cookies, passwords, tokens | masked as `********` |
| `user_resolver` | framework default | `callable(request) -> {"id", "name", "email"}` |
| `default_filter` | `True` | apply the production filter outside `local` |
| `async_writes` | `True` | write on a background thread (set `False` in tests) |
| `auto_prune_hours` | `None` | prune automatically every hour, keeping N hours |

Watcher options:

| Watcher | Options |
|---|---|
| `requests` | `size_limit` (KB, default 64), `ignore_http_methods`, `ignore_status_codes` |
| `queries` | `slow` (ms, default 100), `ignore_patterns` (regex) |
| `logs` | `level` (default `DEBUG`), `ignore_loggers` |
| `exceptions` | `ignore` (class names) |
| `commands` | `ignore` (`runserver`, `shell`, `migrate`, … by default) |
| `events` | `ignore`, `include_framework` (framework signals are skipped by default) |
| `models` | `events` (`created`, `updated`, `deleted`), `hydrations` |
| `cache` | `hidden` (key globs whose values are masked) |
| `gates` | `ignore_abilities` |
| `http_client` | `size_limit` |

## Customising

```python
from tracefox import tracefox, dump

# Tag entries
@tracefox.tag
def tags(entry):
    return ["vip"] if entry.is_request() and entry.content.get("user", {}).get("id") == 1 else []

# Only keep what matters (monitored tags always pass)
tracefox.filter(lambda entry: entry.is_exception() or entry.is_failed_request() or entry.is_slow_query())

# Keep or drop a whole request/job/command batch
tracefox.filter_batch(lambda entries: any(e.is_exception() for e in entries))

tracefox.hide_request_parameters("ssn", "card_number")
tracefox.hide_request_headers("x-internal-token")
```

### Manual instrumentation

Some Laravel concepts have no single standard in Python, so each one also has a small API:

```python
dump(user, order)                                         # Dumps screen; returns its argument
tracefox.record_event("order.shipped", {"id": 7}, listeners=["NotifyCustomer"])
tracefox.record_notification("InvoicePaid", user, "slack", response={"ok": True})
tracefox.record_mail(mailable="WelcomeMail", to=["a@b.c"], subject="Hi", html="<h1>Hi</h1>")
tracefox.record_cache("hit", "user:1", value)
tracefox.record_gate("edit-post", allowed, [post])

@tracefox.gate("publish-post")           # records the boolean result
def can_publish(user, post): ...

@tracefox.job(queue="emails")            # records a processed / failed job and batches its entries
def send_digest(user_id): ...

with tracefox.command("import:users", {"file": path}):   # records a custom command
    ...

with tracefox.batch():                   # group entries recorded outside a request
    ...

with tracefox.without_recording():       # skip recording for a block
    ...
```

## Maintenance

```bash
tracefox prune --hours 48 --keep-exceptions
tracefox clear
tracefox pause | resume
tracefox --database-url postgresql://... migrate
```

Run `prune` on a schedule, as Laravel's docs recommend, or set `auto_prune_hours`.

## How it works

- **Watchers** hook libraries only when your app imports them. A `sys.meta_path` hook runs the callback after import, so tracefox never pulls in Celery or Django by itself. Every patch is reversible with `tracefox.stop()`.
- **Batches** live in a `ContextVar`, so they follow `async` code, threadpools, `asgiref` and Celery tasks correctly. Entries are buffered and written when the request, job or command finishes.
- **Storage** mirrors Laravel Telescope's schema: `tracefox_entries`, `tracefox_entries_tags`, `tracefox_monitoring`, plus `tracefox_settings`. Repeated exceptions are folded into families when they are written.
- **Out-of-order updates.** A job can finish (in a worker, or a background task) before the request that dispatched it is written. That update is applied in memory if the entry is still buffered; otherwise the writer retries it for two minutes.
- **The dashboard** is a dependency-free single-page app served by a framework-agnostic handler, with thin ASGI, WSGI and Django adapters.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
pytest
TRACEFOX_ENV=local uvicorn examples.fastapi_app:app --reload   # demo that exercises every watcher
```

## Releasing

Releases go to PyPI from GitHub Actions through [Trusted Publishing](https://docs.pypi.org/trusted-publishers/), so no API token is stored.

1. Bump `version` in `pyproject.toml` and commit.
2. Tag it and push the tag: `git tag v0.1.1 && git push origin v0.1.1`.
3. The **Publish to PyPI** workflow tests, builds, checks and uploads the release.

## License

MIT — see [LICENSE](LICENSE).
