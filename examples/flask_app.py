"""Demo Flask app.

    TELEPY_ENV=local flask --app examples.flask_app run
    open http://127.0.0.1:5000/telepy
"""

import logging

from flask import Flask, render_template_string, session
from sqlalchemy import create_engine, text

from telepy import dump, telepy
from telepy.integrations.flask import FlaskTelepy

app = Flask(__name__)
app.secret_key = "dev"
app.config["TELEPY"] = {"database_url": "sqlite:///telepy-flask.sqlite3"}
FlaskTelepy(app)

engine = create_engine("sqlite:///demo-flask.sqlite3")
logger = logging.getLogger("flask-demo")


@app.route("/")
def index():
    session["visits"] = session.get("visits", 0) + 1
    with engine.connect() as conn:
        now = conn.execute(text("select datetime('now')")).scalar()
    logger.info("Index visited %s times", session["visits"])
    dump({"visits": session["visits"], "now": now})
    telepy.record_event("page.viewed", {"page": "index"})
    return render_template_string("<h1>Hello from Flask</h1><p>Server time: {{ now }}</p>", now=now)


@app.route("/boom")
def boom():
    raise RuntimeError("Something broke in Flask")
