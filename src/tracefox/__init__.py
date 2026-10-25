"""tracefox — an elegant debug assistant for Python web applications, modelled on Laravel Telescope."""

from typing import Any

from .config import TracefoxConfig
from .core import Tracefox, tracefox
from .entry import EntryType, EntryUpdate, IncomingEntry

__version__ = "0.1.0"


def dump(*values: Any) -> Any:
    """Record values on the Dumps screen and return them (so it can wrap expressions)."""
    return tracefox.dump(*values)


__all__ = [
    "EntryType", "EntryUpdate", "IncomingEntry", "Tracefox", "TracefoxConfig", "dump", "tracefox", "__version__",
]
