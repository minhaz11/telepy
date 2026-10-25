import pytest
from flask import Flask, render_template_string, session

from telepy import EntryType
from telepy.integrations.flask import FlaskTelepy


@pytest.fixture
def app(telepy):
    app = Flask(__name__)
    app.secret_key = "test"
    FlaskTelepy(app, telepy)

    @app.route("/hello/<name>", methods=["GET", "POST"])
    def hello(name):
        session["visits"] = 3
        return render_template_string("Hello {{ name }}", name=name)

    @app.route("/api/data", methods=["POST"])
    def data():
        return {"ok": True}

    @app.route("/boom")
    def boom():
        raise RuntimeError("flask exploded")

    return app


@pytest.fixture
def client(app):
    # Real WSGI servers always iterate the response; werkzeug's test client only does when buffered.
    client = app.test_client()
    open_ = client.open
    client.open = lambda *args, **kwargs: open_(*args, buffered=True, **kwargs)
    return client


def test_request_with_view_session_and_controller(client, entries):
    response = client.post("/hello/ada?x=1", data={"password": "s3cret", "nick": "a"})
    assert response.status_code == 200
    request = entries(EntryType.REQUEST)[0]
    c = request["content"]
    assert c["uri"] == "/hello/ada?x=1"
    assert c["payload"] == {"x": "1", "password": "********", "nick": "a"}
    assert c["session"] == {"visits": 3}
    assert c["response"] == "HTML Response"
    assert c["controller_action"].endswith("hello (/hello/<name>)")
    view = entries(EntryType.VIEW)[0]
    assert view["batch_id"] == request["batch_id"]


def test_json_response(client, entries):
    client.post("/api/data", json={"a": 1})
    c = entries(EntryType.REQUEST)[0]["content"]
    assert c["payload"] == {"a": 1}
    assert c["response"] == {"ok": True}


def test_exception_is_recorded_in_request_batch(client, entries):
    assert client.get("/boom").status_code == 500
    request = entries(EntryType.REQUEST)[0]
    exception = entries(EntryType.EXCEPTION)[0]
    assert request["content"]["response_status"] == 500
    assert exception["content"]["message"] == "flask exploded"
    assert exception["batch_id"] == request["batch_id"]


def test_dashboard_and_api(client, entries):
    client.get("/hello/x")
    assert client.get("/telepy").status_code == 200
    assert client.get("/telepy/requests/abc").status_code == 200
    data = client.get("/telepy/api/entries/request").get_json()
    assert len(data["entries"]) == 1
    assert len(entries(EntryType.REQUEST)) == 1


def test_cli_commands(app, client, entries):
    client.get("/hello/x")
    runner = app.test_cli_runner()
    assert "entries pruned" in runner.invoke(args=["telepy", "prune", "--hours", "0"]).output
    assert entries(EntryType.REQUEST) == []
