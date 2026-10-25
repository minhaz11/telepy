from .base import Watcher
from .batch import BatchWatcher
from .cache import CacheWatcher
from .command import CommandWatcher
from .dump import DumpWatcher
from .event import EventWatcher
from .exception import ExceptionWatcher
from .gate import GateWatcher
from .http_client import HttpClientWatcher
from .job import JobWatcher
from .log import LogWatcher
from .mail import MailWatcher
from .model import ModelWatcher
from .notification import NotificationWatcher
from .query import QueryWatcher
from .redis import RedisWatcher
from .request import RequestWatcher
from .schedule import ScheduleWatcher
from .view import ViewWatcher

# Order matters: exceptions must be installed before logs so log records with exc_info route correctly.
WATCHERS = {
    w.name: w
    for w in (
        RequestWatcher, ExceptionWatcher, LogWatcher, QueryWatcher, ModelWatcher, EventWatcher, JobWatcher,
        BatchWatcher, CacheWatcher, RedisWatcher, HttpClientWatcher, MailWatcher, NotificationWatcher,
        GateWatcher, ViewWatcher, CommandWatcher, ScheduleWatcher, DumpWatcher,
    )
}

__all__ = ["WATCHERS", "Watcher"] + [w.__name__ for w in WATCHERS.values()]
