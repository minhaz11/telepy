import pytest

from telepy import TelepyConfig, telepy as _telepy
from telepy.storage import EntryQueryOptions


@pytest.fixture
def telepy(tmp_path):
    _telepy.stop()
    _telepy.config = TelepyConfig(
        database_url=f"sqlite:///{tmp_path / 'telepy.sqlite3'}", environment="local", async_writes=False,
    )
    _telepy._storage = None
    _telepy._recording_cache = (0.0, True)
    _telepy.forget_monitored_cache()
    _telepy._tag_callbacks.clear()
    _telepy._filters.clear()
    _telepy._batch_filters.clear()
    _telepy.start()
    yield _telepy
    _telepy.stop()


@pytest.fixture
def entries(telepy):
    def fetch(type=None, **options):
        telepy.flush()
        return telepy.storage.get(type, EntryQueryOptions(limit=500, **options))

    return fetch
