import pytest

from tracefox import TracefoxConfig, tracefox as _tracefox
from tracefox.storage import EntryQueryOptions


@pytest.fixture
def tracefox(tmp_path):
    _tracefox.stop()
    _tracefox.config = TracefoxConfig(
        database_url=f"sqlite:///{tmp_path / 'tracefox.sqlite3'}", environment="local", async_writes=False,
    )
    _tracefox._storage = None
    _tracefox._recording_cache = (0.0, True)
    _tracefox.forget_monitored_cache()
    _tracefox._tag_callbacks.clear()
    _tracefox._filters.clear()
    _tracefox._batch_filters.clear()
    _tracefox.start()
    yield _tracefox
    _tracefox.stop()


@pytest.fixture
def entries(tracefox):
    def fetch(type=None, **options):
        tracefox.flush()
        return tracefox.storage.get(type, EntryQueryOptions(limit=500, **options))

    return fetch
