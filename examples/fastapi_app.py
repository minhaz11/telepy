"""Demo FastAPI app that exercises every Telepy watcher.

    TELEPY_ENV=local uvicorn examples.fastapi_app:app --reload
    open http://127.0.0.1:8000/telepy
"""

import logging

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from jinja2 import DictLoader, Environment
from pydantic import BaseModel
from sqlalchemy import ForeignKey, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from telepy import dump, telepy
from telepy.integrations.fastapi import init_telepy

logger = logging.getLogger("demo")

app = FastAPI(debug=True, title="Telepy demo")
init_telepy(app, database_url="sqlite:///telepy-demo.sqlite3")

engine = create_engine("sqlite:///demo-app.sqlite3")


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    email: Mapped[str] = mapped_column(String(120))


class Post(Base):
    __tablename__ = "posts"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(200))


Base.metadata.create_all(engine)

templates = Environment(loader=DictLoader({
    "home.html": "<h1>Hello {{ name }}</h1><ul>{% for u in users %}<li>{{ u.name }}</li>{% endfor %}</ul>",
}))

try:
    import fakeredis

    redis_client = fakeredis.FakeRedis()
except ImportError:  # pragma: no cover
    redis_client = None


class UserIn(BaseModel):
    name: str
    email: str
    password: str


def fake_api(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/weather":
        return httpx.Response(200, json={"city": "Dhaka", "temperature": 31, "conditions": "humid"})
    return httpx.Response(503, json={"error": "upstream unavailable"})


http = httpx.Client(transport=httpx.MockTransport(fake_api), base_url="https://api.example.com")


@telepy.gate("view-dashboard")
def can_view_dashboard(user: User) -> bool:
    return user.id % 2 == 1


@app.get("/", response_class=HTMLResponse)
def home():
    with Session(engine) as session:
        users = session.scalars(select(User).limit(5)).all()
    logger.info("Rendering home page for %d users", len(users))
    return templates.get_template("home.html").render(name="Telepy", users=users)


@app.get("/users")
def list_users(q: str = ""):
    with Session(engine) as session:
        stmt = select(User).order_by(User.id.desc())
        if q:
            stmt = stmt.where(User.name.like(f"%{q}%"))
        users = session.scalars(stmt).all()
        for user in users:
            can_view_dashboard(user)
        return [{"id": u.id, "name": u.name, "email": u.email} for u in users]


@app.post("/users", status_code=201)
def create_user(payload: UserIn, background: BackgroundTasks):
    with Session(engine) as session:
        user = User(name=payload.name, email=payload.email)
        session.add(user)
        session.commit()
        session.add(Post(user_id=user.id, title=f"Welcome {user.name}"))
        user.name = user.name.title()
        session.commit()
        user_id = user.id
    telepy.record_event("user.registered", {"id": user_id, "email": payload.email}, ["SendWelcomeEmail", "SyncCrm"])
    background.add_task(send_welcome_email, user_id, payload.email)
    logger.warning("New signup from %s", payload.email, extra={"user_id": user_id})
    return {"id": user_id}


def send_welcome_email(user_id: int, email: str) -> None:
    with Session(engine) as session:
        session.get(User, user_id)
    telepy.record_mail(
        mailable="WelcomeMail", to=[email], subject="Welcome aboard!", **{"from": ["hello@example.com"]},
        html=f"<h2>Welcome!</h2><p>Thanks for joining, {email}.</p>",
        raw=f"From: hello@example.com\r\nTo: {email}\r\nSubject: Welcome aboard!\r\n\r\nThanks for joining.",
    )
    telepy.record_notification("InvoicePaid", f"User:{user_id}", "slack", {"ok": True})


@app.get("/weather")
def weather():
    data = http.get("/weather", params={"city": "Dhaka"}).json()
    failed = http.post("/forecast", json={"days": 3})
    if redis_client is not None:
        cached = redis_client.get("weather:dhaka")
        if cached is None:
            redis_client.setex("weather:dhaka", 60, str(data["temperature"]))
        pipe = redis_client.pipeline()
        pipe.incr("hits:weather").expire("hits:weather", 3600).execute()
    return {"weather": data, "forecast_status": failed.status_code}


@app.get("/dump")
def dumps(request: Request):
    dump({"query": dict(request.query_params), "client": request.client.host if request.client else None})
    dump([1, 2, 3, {"nested": True}])
    return {"dumped": True}


@app.get("/slow")
def slow():
    with Session(engine) as session:
        session.execute(select(User).where(User.id > 0)).all()
        session.connection().exec_driver_sql(
            "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 300000) SELECT count(*) FROM c"
        ).scalar()
    return {"ok": True}


@app.get("/missing")
def missing():
    raise HTTPException(status_code=404, detail="Nothing here")


@app.get("/boom")
def boom():
    with Session(engine) as session:
        session.scalars(select(User)).first()
    order = {"id": 42, "total": 99.5}
    return {"result": order["discount"] / 0}


@app.get("/log-error")
def log_error():
    try:
        int("not-a-number")
    except ValueError:
        logger.exception("Could not parse the quantity")
    logger.error("Payment gateway timed out", extra={"gateway": "stripe", "attempt": 3})
    return {"logged": True}
