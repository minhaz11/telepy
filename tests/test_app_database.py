import os
import sqlite3

import pytest
import sqlalchemy as sa

from tracefox import EntryType
from tracefox.cli import main as cli
from tracefox.storage import DatabaseStorage

TABLES = {"tracefox_entries", "tracefox_entries_tags", "tracefox_monitoring", "tracefox_settings"}


def table_names(path):
    with sqlite3.connect(path) as conn:
        return {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}


def test_entries_go_to_the_applications_engine(tracefox, entries, tmp_path):
    app_db = tmp_path / "app.sqlite3"
    engine = sa.create_engine(f"sqlite:///{app_db}")
    tracefox.configure(engine=engine)
    assert tracefox.storage.engine is engine
    with tracefox.batch():
        with engine.connect() as conn:
            conn.execute(sa.text("select 42"))
    assert TABLES <= table_names(app_db)
    sqls = [q["content"]["sql"] for q in entries(EntryType.QUERY)]
    assert sqls == ["select 42"]
    assert not getattr(engine, "_tracefox_storage", False)


def test_engine_can_be_a_callable(tracefox, entries, tmp_path):
    app_db = tmp_path / "lazy.sqlite3"
    tracefox.configure(engine=lambda: sa.create_engine(f"sqlite:///{app_db}"))
    tracefox.record_event("lazy", {})
    assert len(entries(EntryType.EVENT)) == 1
    assert TABLES <= table_names(app_db)


def test_async_engine_gets_a_sync_twin_on_the_same_database(tracefox, entries, tmp_path):
    pytest.importorskip("aiosqlite")
    from sqlalchemy.ext.asyncio import create_async_engine

    app_db = tmp_path / "async.sqlite3"
    tracefox.configure(engine=create_async_engine(f"sqlite+aiosqlite:///{app_db}"))
    tracefox.record_event("async", {})
    assert tracefox.storage.engine.url.drivername == "sqlite"
    assert tracefox.storage.engine.url.database == str(app_db)
    assert len(entries(EntryType.EVENT)) == 1


def test_async_engine_without_a_sync_driver_explains_what_to_install():
    url = sa.engine.make_url("sybase+asyncnothing://u:p@localhost/db")
    storage = DatabaseStorage(engine=type("FakeAsyncEngine", (), {"sync_engine": object(), "url": url})())
    with pytest.raises(RuntimeError, match="synchronous driver"):
        storage.engine


@pytest.mark.parametrize("tracefox_first", [True, False])
def test_flask_sqlalchemy_database_is_used(tracefox, entries, tmp_path, tracefox_first):
    pytest.importorskip("flask_sqlalchemy")
    from flask import Flask
    from flask_sqlalchemy import SQLAlchemy

    from tracefox.integrations.flask import FlaskTracefox

    app_db = tmp_path / "flask.sqlite3"
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{app_db}"
    db = SQLAlchemy()
    if tracefox_first:
        FlaskTracefox(app, tracefox)
        db.init_app(app)
    else:
        db.init_app(app)
        FlaskTracefox(app, tracefox)

    @app.route("/")
    def index():
        db.session.execute(sa.text("select 1"))
        return "ok"

    app.test_client().get("/", buffered=True)
    assert TABLES <= table_names(app_db)
    assert [r["content"]["uri"] for r in entries(EntryType.REQUEST)] == ["/"]
    with app.app_context():
        assert tracefox.storage.engine is db.engine


def test_alembic_install_upgrade_and_downgrade(tmp_path, monkeypatch):
    pytest.importorskip("alembic")
    from alembic import command
    from alembic.config import Config

    from tracefox.integrations.alembic import include_object

    monkeypatch.chdir(tmp_path)
    command.init(Config("alembic.ini"), "migrations")
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path / 'app.sqlite3'}")
    with open("alembic.ini", "w") as fh:
        cfg.file_config.write(fh)
    command.revision(Config("alembic.ini"), "create users", rev_id="users0001")

    assert cli(["install"]) == 0
    created = [f for f in os.listdir("migrations/versions") if f.endswith("_create_tracefox_tables.py")]
    assert len(created) == 1
    assert "down_revision = 'users0001'" in open(f"migrations/versions/{created[0]}").read()
    assert cli(["install"]) == 0  # already installed: no second migration
    assert len([f for f in os.listdir("migrations/versions") if "tracefox" in f]) == 1

    command.upgrade(Config("alembic.ini"), "head")
    assert TABLES <= table_names(tmp_path / "app.sqlite3")
    command.downgrade(Config("alembic.ini"), "users0001")
    assert not TABLES & table_names(tmp_path / "app.sqlite3")
    command.upgrade(Config("alembic.ini"), "head")

    storage = DatabaseStorage(f"sqlite:///{tmp_path / 'app.sqlite3'}", auto_migrate=False)
    storage.monitor(["Auth:1"])
    assert storage.monitored_tags() == ["Auth:1"]

    table = sa.Table("tracefox_entries", sa.MetaData())
    assert include_object(table, "tracefox_entries", "table", True, None) is False
    assert include_object(sa.Table("users", sa.MetaData()), "users", "table", True, None) is True


def test_alembic_migration_is_idempotent_with_auto_migrate(tmp_path, monkeypatch):
    pytest.importorskip("alembic")
    from alembic import command
    from alembic.config import Config

    monkeypatch.chdir(tmp_path)
    url = f"sqlite:///{tmp_path / 'app.sqlite3'}"
    DatabaseStorage(url).migrate()  # tables created by auto-migrate first
    command.init(Config("alembic.ini"), "migrations")
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    with open("alembic.ini", "w") as fh:
        cfg.file_config.write(fh)
    assert cli(["install"]) == 0
    command.upgrade(Config("alembic.ini"), "head")
    assert TABLES <= table_names(tmp_path / "app.sqlite3")


def test_install_without_alembic_project_fails_clearly(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli(["install"]) == 1
    assert "No Alembic migrations found" in capsys.readouterr().err
