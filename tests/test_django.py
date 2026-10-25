import logging

import django
import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        SECRET_KEY="test",
        ALLOWED_HOSTS=["*"],
        ROOT_URLCONF=__name__,
        INSTALLED_APPS=[
            "django.contrib.contenttypes", "django.contrib.auth", "django.contrib.sessions",
            "tracefox.integrations.django",
        ],
        MIDDLEWARE=[
            "tracefox.integrations.django.middleware.TracefoxMiddleware",
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
        ],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        TEMPLATES=[{
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", {"hello.html": "Hi {{ name }}"})]},
        }],
        TRACEFOX={"async_writes": False},
        USE_TZ=True,
    )
    django.setup()

from django.contrib.auth.models import AnonymousUser, User  # noqa: E402
from django.core.cache import cache  # noqa: E402
from django.core.mail import send_mail  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.dispatch import Signal  # noqa: E402
from django.http import JsonResponse  # noqa: E402
from django.shortcuts import render  # noqa: E402
from django.test import Client  # noqa: E402
from django.urls import include, path  # noqa: E402

from tracefox import EntryType  # noqa: E402
from tracefox.integrations.django.models import Entry, MonitoredTag, Setting  # noqa: E402

order_placed = Signal()


def hello(request, name):
    cache.get("greeting")
    cache.set("greeting", name, 30)
    logging.getLogger("tests.django").info("hello %s", name)
    AnonymousUser().has_perm("blog.change_post")
    request.session["seen"] = True
    list(User.objects.all())
    return render(request, "hello.html", {"name": name})


def create_user(request):
    user = User.objects.create(username=request.POST["username"])
    user.first_name = "Ada"
    user.save()
    send_mail("Welcome", "Hi there", "app@example.com", ["ada@example.com"])
    order_placed.send(sender=None, order_id=5)
    return JsonResponse({"id": user.pk}, status=201)


def boom(request):
    raise ValueError("django exploded")


urlpatterns = [
    path("hello/<str:name>/", hello),
    path("users/", create_user),
    path("boom/", boom),
    path("tracefox/", include("tracefox.integrations.django.urls")),
]


@pytest.fixture
def client(tracefox):
    from tracefox.integrations.django.storage import DjangoStorage

    call_command("migrate", run_syncdb=True, verbosity=0)
    tracefox.storage = DjangoStorage()
    tracefox.storage.clear()
    with tracefox.without_recording():
        MonitoredTag.objects.all().delete()
        Setting.objects.all().delete()
    return Client(raise_request_exception=False)


def test_request_and_related_entries(client, entries):
    assert client.get("/hello/ada/?ref=x").status_code == 200
    request = entries(EntryType.REQUEST)[0]
    c = request["content"]
    assert (c["method"], c["uri"], c["response_status"]) == ("GET", "/hello/ada/?ref=x", 200)
    assert c["payload"] == {"ref": "x"}
    assert c["session"] == {"seen": True}
    assert "hello" in c["controller_action"]
    batch = {e["type"] for e in entries() if e["batch_id"] == request["batch_id"]}
    assert {EntryType.CACHE, EntryType.LOG, EntryType.GATE, EntryType.QUERY, EntryType.VIEW} <= batch
    cache_actions = [e["content"]["type"] for e in entries(EntryType.CACHE)]
    assert cache_actions == ["set", "missed"]
    view = entries(EntryType.VIEW)[0]["content"]
    assert view["name"] == "hello.html" and view["data"] == ["name"]


def test_models_mail_and_signals(client, entries):
    response = client.post("/users/", {"username": "ada", "password": "secret"})
    assert response.status_code == 201
    request = entries(EntryType.REQUEST)[0]["content"]
    assert request["payload"] == {"username": "ada", "password": "********"}
    models = {e["content"]["action"]: e["content"] for e in entries(EntryType.MODEL)}
    assert models["created"]["model"] == "django.contrib.auth.models.User:1"
    assert models["updated"]["changes"] == {"first_name": "Ada"}
    mail = entries(EntryType.MAIL)[0]["content"]
    assert mail["subject"] == "Welcome" and mail["to"] == ["ada@example.com"]
    assert "Subject: Welcome" in mail["raw"]
    event = entries(EntryType.EVENT)[0]["content"]
    assert event["name"].endswith("order_placed")
    assert event["payload"]["order_id"] == 5


def test_exceptions_are_recorded(client, entries):
    assert client.get("/boom/").status_code == 500
    request = entries(EntryType.REQUEST)[0]
    exception = entries(EntryType.EXCEPTION)[0]
    assert request["content"]["response_status"] == 500
    assert exception["content"]["message"] == "django exploded"
    assert exception["batch_id"] == request["batch_id"]


def test_dashboard_is_served_and_not_recorded(client, entries):
    client.get("/hello/x/")
    assert client.get("/tracefox/").status_code == 200
    assert client.get("/tracefox/queries").status_code == 200
    api = client.get("/tracefox/api/entries/request").json()
    assert len(api["entries"]) == 1
    toggled = client.post("/tracefox/api/toggle-recording", HTTP_X_TRACEFOX_REQUEST="1")
    assert toggled.json() == {"recording": False}
    client.post("/tracefox/api/toggle-recording", HTTP_X_TRACEFOX_REQUEST="1")
    assert len(entries(EntryType.REQUEST)) == 1


def test_management_commands(client, entries):
    call_command("diffsettings", verbosity=0, stdout=__import__("io").StringIO())
    command = entries(EntryType.COMMAND)[0]["content"]
    assert command["command"] == "diffsettings"
    assert command["exit_code"] == 0
    call_command("tracefox_clear", stdout=__import__("io").StringIO())
    assert entries(EntryType.COMMAND) == []


def test_entries_are_stored_in_the_project_database(client, entries, tracefox):
    from django.db import connection

    client.get("/hello/ada/")
    tables = set(connection.introspection.table_names())
    assert {"tracefox_entries", "tracefox_entries_tags", "tracefox_monitoring", "tracefox_settings"} <= tables
    with tracefox.without_recording():
        assert Entry.objects.filter(type=EntryType.REQUEST).count() == 1
    assert [q["content"]["sql"] for q in entries(EntryType.QUERY) if "tracefox_" in q["content"]["sql"]] == []
    assert not [m for m in entries(EntryType.MODEL) if "tracefox" in m["content"]["model"]]


def test_app_config_uses_the_default_connection(tracefox):
    from django.apps import apps

    from tracefox.integrations.django.storage import DjangoStorage

    tracefox._storage = None
    apps.get_app_config("tracefox").ready()
    assert isinstance(tracefox.storage, DjangoStorage)
    assert tracefox.storage.using == "default"


def test_shipped_migrations_match_the_models(tracefox):
    call_command("makemigrations", "tracefox", check=True, dry_run=True, verbosity=0)
