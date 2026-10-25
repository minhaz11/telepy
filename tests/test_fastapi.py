import logging

import pytest
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.testclient import TestClient

from tracefox import EntryType
from tracefox.integrations.fastapi import init_tracefox


@pytest.fixture
def app(tracefox):
    app = FastAPI()
    init_tracefox(app, tracefox)

    @app.get("/items/{item_id}")
    def read_item(item_id: int, q: str = ""):
        logging.getLogger("tests.api").warning("reading %s", item_id)
        return {"id": item_id, "q": q}

    @app.post("/login")
    def login(payload: dict):
        return {"token": "abc"}

    @app.get("/missing")
    def missing():
        raise HTTPException(404, "nope")

    @app.get("/boom")
    def boom():
        raise ZeroDivisionError("kaboom")

    def task(n):
        logging.getLogger("tests.jobs").info("job ran %s", n)

    @app.post("/queue")
    def queue(background: BackgroundTasks):
        background.add_task(task, 5)
        return {"queued": True}

    return app


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


def test_request_is_recorded_with_related_entries(client, entries):
    assert client.get("/items/7?q=x", headers={"Authorization": "Bearer t"}).json() == {"id": 7, "q": "x"}
    request = entries(EntryType.REQUEST)[0]
    c = request["content"]
    assert (c["method"], c["uri"], c["response_status"]) == ("GET", "/items/7?q=x", 200)
    assert c["response"] == {"id": 7, "q": "x"}
    assert c["payload"] == {"q": "x"}
    assert c["headers"]["authorization"] == "********"
    assert "read_item" in c["controller_action"] and "/items/{item_id}" in c["controller_action"]
    log = entries(EntryType.LOG)[0]
    assert log["batch_id"] == request["batch_id"]


def test_sensitive_payload_is_hidden(client, entries):
    client.post("/login", json={"email": "a@b.c", "password": "hunter2"})
    payload = entries(EntryType.REQUEST)[0]["content"]["payload"]
    assert payload == {"email": "a@b.c", "password": "********"}


def test_unhandled_exception_records_exception_and_500(client, entries):
    assert client.get("/boom").status_code == 500
    request = entries(EntryType.REQUEST)[0]
    exception = entries(EntryType.EXCEPTION)[0]
    assert request["content"]["response_status"] == 500
    assert exception["content"]["class"] == "ZeroDivisionError"
    assert exception["batch_id"] == request["batch_id"]
    assert exception["content"]["file"].endswith("test_fastapi.py")


def test_http_exception_is_not_an_exception_entry(client, entries):
    assert client.get("/missing").status_code == 404
    assert entries(EntryType.EXCEPTION) == []
    assert entries(EntryType.REQUEST)[0]["content"]["response_status"] == 404


def test_background_task_is_recorded_as_processed_job(client, entries):
    client.post("/queue")
    job = entries(EntryType.JOB)[0]
    request = entries(EntryType.REQUEST)[0]
    assert job["content"]["status"] == "processed"
    assert job["content"]["name"].endswith("task")
    assert job["batch_id"] == request["batch_id"]
    log = entries(EntryType.LOG)[0]
    assert log["batch_id"] == job["content"]["processing_batch_id"]


def test_dashboard_requests_are_not_recorded(client, entries):
    assert client.get("/tracefox").status_code == 200
    assert client.get("/tracefox/requests").status_code == 200
    assert entries(EntryType.REQUEST) == []


def test_dashboard_api(client, entries, tracefox):
    client.get("/items/1")
    data = client.get("/tracefox/api/entries/request").json()
    assert len(data["entries"]) == 1
    uuid = data["entries"][0]["id"]
    show = client.get(f"/tracefox/api/entries/request/{uuid}").json()
    assert show["entry"]["id"] == uuid
    assert [e["type"] for e in show["batch"]] == ["log"]
    assert client.get("/tracefox/api/status").json()["counts"]["request"] == 1

    assert client.post("/tracefox/api/toggle-recording").status_code == 419
    headers = {"X-Tracefox-Request": "1"}
    assert client.post("/tracefox/api/toggle-recording", headers=headers).json() == {"recording": False}
    client.get("/items/2")
    assert len(entries(EntryType.REQUEST)) == 1
    client.post("/tracefox/api/toggle-recording", headers=headers)

    assert client.post("/tracefox/api/monitored-tags", json={"tag": "Auth:1"}, headers=headers).json() == {"tags": ["Auth:1"]}
    assert client.request("DELETE", "/tracefox/api/monitored-tags", json={"tag": "Auth:1"}, headers=headers).json() == {"tags": []}
    assert client.request("DELETE", "/tracefox/api/entries", headers=headers).status_code == 200
    assert [e["type"] for e in entries()] == ["client_request"]  # the TestClient's own DELETE call


def test_static_assets_and_spa_routes(client):
    assert client.get("/tracefox/static/app.js").headers["content-type"].startswith("text/javascript")
    page = client.get("/tracefox/exceptions/abc")
    assert page.status_code == 200
    assert '"path": "/tracefox"' in page.text


def test_dashboard_is_forbidden_outside_local_without_authorization(client, tracefox):
    tracefox.config.environment = "production"
    assert client.get("/tracefox").status_code == 403
    assert client.get("/tracefox/api/entries/request").status_code == 403
    tracefox.config.authorization = lambda request: request.headers.get("x-admin") == "yes"
    assert client.get("/tracefox/api/entries/request", headers={"x-admin": "yes"}).status_code == 200
