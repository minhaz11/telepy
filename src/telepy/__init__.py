"""telepy — an elegant debug assistant for Python web applications, modelled on Laravel Telescope."""

from typing import Any

from .config import TelepyConfig
from .core import Telepy, telepy
from .entry import EntryType, EntryUpdate, IncomingEntry

__version__ = "0.1.0"


def dump(*values: Any) -> Any:
    """Record values on the Dumps screen and return them (so it can wrap expressions)."""
    return telepy.dump(*values)


__all__ = [
    "EntryType", "EntryUpdate", "IncomingEntry", "Telepy", "TelepyConfig", "dump", "telepy", "__version__",
]
