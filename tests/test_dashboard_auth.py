import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tracefox import EntryType, IncomingEntry, TracefoxConfig
from tracefox.integrations.fastapi import init_tracefox

PROTECTED = ["development", "dev", "stage", "staging", "uat", "production"]


@pytest.fixture
def client(tracefox):
    app = FastAPI()
    init_tracefox(app, tracefox)
    return TestClient(app)


def basic(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def test_local_is_open_without_login(client, tracefox):
    tracefox.config.environment = "local"
    assert client.get("/tracefox").status_code == 200
    assert client.get("/tracefox/api/entries/request").status_code == 200


@pytest.mark.parametrize("env", PROTECTED)
def test_other_environments_are_locked_without_credentials(client, tracefox, env):
    tracefox.config.environment = env
    page = client.get("/tracefox")
    assert page.status_code == 403
    assert f"<code>{env}</code>" in page.text
    assert client.get("/tracefox/api/entries/request").status_code == 403


@pytest.mark.parametrize("env", PROTECTED)
def test_basic_auth_prompts_and_accepts_the_right_password(client, tracefox, env):
    tracefox.config.environment = env
    tracefox.config.basic_auth = ("admin", "s3cret")
    challenge = client.get("/tracefox")
    assert challenge.status_code == 401
    assert challenge.headers["www-authenticate"].startswith('Basic realm="TraceFox"')
    assert client.get("/tracefox", headers=basic("admin", "wrong")).status_code == 401
    assert client.get("/tracefox", headers=basic("root", "s3cret")).status_code == 401
    assert client.get("/tracefox", headers=basic("admin", "s3cret")).status_code == 200
    assert client.get("/tracefox/api/entries/request", headers=basic("admin", "s3cret")).status_code == 200
    assert client.get("/tracefox/api/entries/request").status_code == 401


def test_basic_auth_supports_several_users_and_a_string(client, tracefox):
    tracefox.config.environment = "staging"
    tracefox.config.basic_auth = {"ada": "one", "grace": "two"}
    assert client.get("/tracefox", headers=basic("grace", "two")).status_code == 200
    assert client.get("/tracefox", headers=basic("ada", "two")).status_code == 401
    tracefox.config.basic_auth = "ops:pa:ss"
    assert client.get("/tracefox", headers=basic("ops", "pa:ss")).status_code == 200


def test_credentials_come_from_environment_variables(monkeypatch):
    monkeypatch.setenv("TRACEFOX_USERNAME", "admin")
    monkeypatch.setenv("TRACEFOX_PASSWORD", "from-env")
    assert TracefoxConfig().basic_auth_users() == {"admin": "from-env"}
    monkeypatch.delenv("TRACEFOX_PASSWORD")
    assert TracefoxConfig().basic_auth_users() == {}


def test_authorization_callback_decides_when_set(client, tracefox):
    tracefox.config.environment = "uat"
    tracefox.config.authorization = lambda request: request.headers.get("x-team") == "qa"
    assert client.get("/tracefox").status_code == 403
    assert client.get("/tracefox", headers={"x-team": "qa"}).status_code == 200


def test_basic_auth_and_callback_must_both_pass(client, tracefox):
    tracefox.config.environment = "production"
    tracefox.config.basic_auth = ("admin", "s3cret")
    tracefox.config.authorization = lambda request: request.headers.get("x-team") == "ops"
    assert client.get("/tracefox", headers={"x-team": "ops"}).status_code == 401
    assert client.get("/tracefox", headers=basic("admin", "s3cret")).status_code == 403
    assert client.get("/tracefox", headers={**basic("admin", "s3cret"), "x-team": "ops"}).status_code == 200


def test_open_environments_are_configurable(client, tracefox):
    tracefox.config.environment = "dev"
    tracefox.config.open_environments = ["local", "dev"]
    assert client.get("/tracefox").status_code == 200
    tracefox.config.environment = "local"
    tracefox.config.open_environments = []
    assert client.get("/tracefox").status_code == 403


def test_static_assets_load_before_login(client, tracefox):
    tracefox.config.environment = "staging"
    tracefox.config.basic_auth = ("admin", "s3cret")
    assert client.get("/tracefox/static/app.css").status_code == 200


@pytest.mark.parametrize("env", ["local", "dev", "development", "stage", "staging", "uat"])
def test_dev_and_staging_environments_record_everything(tracefox, entries, env):
    tracefox.config.environment = env
    tracefox.record(IncomingEntry(EntryType.EVENT, {"name": "kept"}))
    assert len(entries(EntryType.EVENT)) == 1


def test_production_keeps_only_important_entries(tracefox, entries):
    tracefox.config.environment = "production"
    tracefox.record(IncomingEntry(EntryType.EVENT, {"name": "dropped"}))
    tracefox.record(IncomingEntry(EntryType.EXCEPTION, {"class": "E", "message": "kept"}))
    assert entries(EntryType.EVENT) == []
    assert len(entries(EntryType.EXCEPTION)) == 1
