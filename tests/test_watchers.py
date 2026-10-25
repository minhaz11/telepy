import asyncio
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from telepy import EntryType


class Base(DeclarativeBase):
    pass


class Book(Base):
    __tablename__ = "books"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(sa.String(50))


@pytest.fixture
def engine():
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


def test_queries_are_recorded_with_bindings_location_and_slow_tag(telepy, entries, engine):
    telepy.watcher("queries").options["slow"] = 0
    with engine.connect() as conn:
        conn.execute(sa.text("select :name as n, :age as a"), {"name": "O'Brien", "age": 3})
    query = entries(EntryType.QUERY)[0]
    assert query["content"]["sql"] == "select 'O''Brien' as n, 3 as a"
    assert query["content"]["slow"] is True
    assert query["content"]["file"].endswith("test_watchers.py")
    assert telepy.storage.find(query["id"])["tags"] == ["slow"]


def test_telepy_own_queries_are_not_recorded(telepy, entries):
    telepy.storage.monitored_tags()
    assert entries(EntryType.QUERY) == []


def test_sqlalchemy_model_events_and_hydrations(telepy, entries, engine):
    with telepy.batch():
        with Session(engine) as session:
            book = Book(title="Dune")
            session.add(book)
            session.commit()
            book.title = "Dune Messiah"
            session.commit()
        with Session(engine) as session:
            session.scalars(sa.select(Book)).all()
            session.delete(session.get(Book, 1))
            session.commit()
    actions = {r["content"]["action"]: r["content"] for r in entries(EntryType.MODEL)}
    assert actions["created"]["model"].endswith("test_watchers.Book:1")
    assert actions["created"]["changes"] == {"id": 1, "title": "Dune"}
    assert actions["updated"]["changes"] == {"title": "Dune Messiah"}
    assert "deleted" in actions
    assert actions["retrieved"]["count"] >= 1


def test_logs_and_exceptions_from_logging(telepy, entries):
    log = logging.getLogger("tests.app")
    log.info("user %s signed in", "ada", extra={"ip": "127.0.0.1"})
    try:
        raise KeyError("missing")
    except KeyError:
        log.exception("lookup failed")
    logging.getLogger("uvicorn.access").info("ignored")
    logs = entries(EntryType.LOG)
    assert len(logs) == 1
    assert logs[0]["content"]["message"] == "user ada signed in"
    assert logs[0]["content"]["context"] == {"ip": "127.0.0.1"}
    exception = entries(EntryType.EXCEPTION)[0]["content"]
    assert exception["class"] == "KeyError"
    assert exception["context"]["log_message"] == "lookup failed"
    assert exception["line_preview"]


def test_exception_is_recorded_once(telepy, entries):
    exc = RuntimeError("once")
    telepy.watcher("exceptions").record_exception(exc)
    telepy.watcher("exceptions").record_exception(exc)
    assert len(entries(EntryType.EXCEPTION, family_hash=None)) == 1


def test_httpx_sync_and_async_clients(telepy, entries):
    telepy.hide_response_parameters("token")

    def handler(request):
        return httpx.Response(201, json={"ok": True, "token": "secret"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        client.post("https://api.test/items", json={"name": "x"}, headers={"Authorization": "Bearer abc"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await client.get("https://api.test/items?page=2")

    asyncio.run(run())
    rows = entries(EntryType.CLIENT_REQUEST)
    assert [r["content"]["method"] for r in rows] == ["GET", "POST"]
    post = rows[1]["content"]
    assert post["payload"] == {"name": "x"}
    assert post["headers"]["authorization"] == "********"
    assert post["response_status"] == 201
    assert post["response"] == {"ok": True, "token": "********"}


def test_requests_library(telepy, entries):
    import requests

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"pong")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        requests.get(f"http://127.0.0.1:{server.server_port}/ping")
    finally:
        server.shutdown()
    row = entries(EntryType.CLIENT_REQUEST)[0]["content"]
    assert row["response"] == "pong"
    assert row["response_status"] == 200


def test_redis_commands_and_pipelines(telepy, entries):
    fakeredis = pytest.importorskip("fakeredis")
    client = fakeredis.FakeRedis()
    client.set("greeting", "hello")
    client.get("greeting")
    client.pipeline().incr("n").expire("n", 10).execute()
    commands = [r["content"]["command"] for r in entries(EntryType.REDIS)]
    assert "SET greeting hello" in commands
    assert "GET greeting" in commands
    assert any(c.startswith("PIPELINE INCRBY n 1; EXPIRE n 10") for c in commands)


def test_jinja_views(telepy, entries):
    from jinja2 import DictLoader, Environment

    env = Environment(loader=DictLoader({"hello.html": "Hi {{ name }}"}))
    assert env.get_template("hello.html").render(name="Ada", role="admin") == "Hi Ada"
    view = entries(EntryType.VIEW)[0]["content"]
    assert view["name"] == "hello.html"
    assert view["data"] == ["name", "role"]


def test_blinker_events(telepy, entries):
    from blinker import Namespace

    signals = Namespace()
    order_shipped = signals.signal("order-shipped")

    def notify(sender, **kwargs):
        pass

    order_shipped.connect(notify)
    order_shipped.send("shop", order_id=7)
    event = entries(EntryType.EVENT)[0]["content"]
    assert event["name"] == "order-shipped"
    assert event["payload"] == {"sender": "shop", "order_id": 7}
    assert event["listeners"][0]["name"].endswith("notify")


def test_gates_notifications_and_manual_mail(telepy, entries):
    @telepy.gate("edit-post")
    def can_edit(user_id):
        return user_id == 1

    assert can_edit(1) is True
    assert can_edit(2) is False
    telepy.record_notification("InvoicePaid", "User:1", "mail", {"id": "abc"})
    telepy.record_mail(mailable="Welcome", to=["a@b.c"], subject="Hi", html="<b>hi</b>")
    assert [g["content"]["result"] for g in entries(EntryType.GATE)] == ["denied", "allowed"]
    assert entries(EntryType.NOTIFICATION)[0]["content"]["notifiable"] == "User:1"
    mail = entries(EntryType.MAIL)[0]
    assert mail["content"]["to"] == ["a@b.c"]
    assert sorted(telepy.storage.find(mail["id"])["tags"]) == ["Welcome", "a@b.c"]


def test_job_decorator_records_processed_and_failed_jobs(telepy, entries):
    @telepy.job(queue="emails")
    def send(to):
        if to == "bad":
            raise ValueError("bounced")
        return to

    with telepy.batch():
        send("ok")
        with pytest.raises(ValueError):
            send("bad")
    jobs = {r["content"]["data"]["args"][0]: r["content"] for r in entries(EntryType.JOB)}
    assert jobs["ok"]["status"] == "processed"
    assert jobs["bad"]["status"] == "failed"
    assert "bounced" in jobs["bad"]["exception"]["message"]


def test_click_commands(telepy, entries):
    import click
    from click.testing import CliRunner

    @click.command("greet")
    @click.argument("name")
    @click.option("--shout", is_flag=True)
    def greet(name, shout):
        logging.getLogger("tests.cli").info("greeting %s", name)
        if name == "fail":
            raise SystemExit(3)

    runner = CliRunner()
    runner.invoke(greet, ["ada", "--shout"])
    runner.invoke(greet, ["fail"])
    commands = entries(EntryType.COMMAND)
    assert [(c["content"]["command"], c["content"]["exit_code"]) for c in commands] == [("greet", 3), ("greet", 0)]
    assert commands[1]["content"]["arguments"] == {"name": "ada"}
    assert commands[1]["content"]["options"] == {"shout": True}
    log = entries(EntryType.LOG)[-1]
    assert log["batch_id"] == commands[1]["batch_id"]


def test_apscheduler_jobs(telepy, entries):
    pytest.importorskip("apscheduler")
    from apscheduler.schedulers.background import BackgroundScheduler

    done = threading.Event()

    def nightly_report():
        done.set()
        return "sent"

    scheduler = BackgroundScheduler()
    scheduler.add_job(nightly_report, "interval", seconds=60, id="report", next_run_time=__import__("datetime").datetime.now())
    scheduler.start()
    try:
        assert done.wait(5)
        for _ in range(50):
            if entries(EntryType.SCHEDULED_TASK):
                break
            threading.Event().wait(0.05)
    finally:
        scheduler.shutdown()
    task = entries(EntryType.SCHEDULED_TASK)[0]["content"]
    assert task["command"].endswith("nightly_report")
    assert task["description"] == "report"
    assert "interval" in task["expression"]
    assert task["output"] == "'sent'"


def test_unregister_restores_patches(telepy):
    import httpx as module

    patched = module.Client.send
    telepy.stop()
    assert module.Client.send is not patched
    assert not getattr(module.Client.send, "__telepy_patched__", False)
    telepy.start()
