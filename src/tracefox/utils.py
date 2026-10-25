from __future__ import annotations

import dataclasses
import datetime as _dt
import decimal
import enum
import fnmatch
import hashlib
import json
import linecache
import os
import socket
import sys
import sysconfig
import traceback
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

HIDDEN = "********"
_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
_STDLIB_DIRS = tuple(
    os.path.abspath(p) for p in {sysconfig.get_paths().get("stdlib"), sysconfig.get_paths().get("platstdlib")} if p
)


def hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:  # pragma: no cover
        return "unknown"


def md5(value: str) -> str:
    return hashlib.md5(value.encode("utf-8", "replace")).hexdigest()


def class_name(obj: Any) -> str:
    cls = obj if isinstance(obj, type) else type(obj)
    module = getattr(cls, "__module__", "")
    name = getattr(cls, "__qualname__", cls.__name__)
    return name if module in ("builtins", "__main__", None, "") else f"{module}.{name}"


def callable_name(fn: Any) -> str:
    fn = getattr(fn, "__func__", fn)
    module = getattr(fn, "__module__", None) or ""
    name = getattr(fn, "__qualname__", None) or getattr(fn, "__name__", None) or repr(fn)
    return f"{module}.{name}" if module else name


def model_key(obj: Any) -> Optional[str]:
    """`app.models.User:1` for Django / SQLAlchemy model instances."""
    meta = getattr(obj, "_meta", None)
    if meta is not None and hasattr(obj, "pk") and hasattr(meta, "concrete_fields"):
        return f"{class_name(obj)}:{obj.pk}"
    mapper = getattr(type(obj), "__mapper__", None)
    if mapper is not None:
        try:
            from sqlalchemy import inspect as sa_inspect

            identity = sa_inspect(obj).identity or mapper.primary_key_from_instance(obj)
            pk = ",".join(str(v) for v in identity) if identity and None not in identity else None
        except Exception:
            pk = None
        return f"{class_name(obj)}:{pk}" if pk is not None else class_name(obj)
    return None


def to_jsonable(value: Any, depth: int = 4, max_items: int = 100, max_str: int = 10000) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            return str(value)
        return value
    if isinstance(value, str):
        return value if len(value) <= max_str else value[:max_str] + "…"
    if isinstance(value, bytes):
        try:
            return to_jsonable(value.decode("utf-8"), depth, max_items, max_str)
        except UnicodeDecodeError:
            return f"<{len(value)} bytes binary>"
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, _dt.timedelta):
        return value.total_seconds()
    if isinstance(value, (decimal.Decimal, uuid.UUID)):
        return str(value)
    if isinstance(value, enum.Enum):
        return to_jsonable(value.value, depth, max_items, max_str)
    if depth <= 0:
        return _short_repr(value)
    if isinstance(value, dict):
        out = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= max_items:
                out["…"] = f"{len(value) - max_items} more"
                break
            out[str(k)] = to_jsonable(v, depth - 1, max_items, max_str)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        out_list = [to_jsonable(v, depth - 1, max_items, max_str) for v in items[:max_items]]
        if len(items) > max_items:
            out_list.append(f"… {len(items) - max_items} more")
        return out_list
    key = model_key(value)
    if key:
        return key
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(dataclasses.asdict(value), depth - 1, max_items, max_str)
    for dumper in ("model_dump", "dict"):
        method = getattr(value, dumper, None)
        if callable(method) and type(value).__module__ != "builtins":
            try:
                return to_jsonable(method(), depth - 1, max_items, max_str)
            except Exception:
                break
    return _short_repr(value)


def _short_repr(value: Any, limit: int = 500) -> str:
    try:
        text = repr(value)
    except Exception:
        text = f"<{class_name(value)}>"
    return text if len(text) <= limit else text[:limit] + "…"


def json_dumps(value: Any) -> str:
    return json.dumps(to_jsonable(value, depth=12, max_items=1000, max_str=10 ** 7), default=_short_repr)


def hide(data: Any, keys: Iterable[str]) -> Any:
    keys = {k.lower() for k in keys}
    if not keys:
        return data
    if isinstance(data, dict):
        return {k: (HIDDEN if str(k).lower() in keys else hide(v, keys)) for k, v in data.items()}
    if isinstance(data, list):
        return [hide(v, keys) for v in data]
    return data


def path_matches(path: str, patterns: Iterable[str]) -> bool:
    for pattern in patterns or ():
        if fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(path.rstrip("/"), pattern.rstrip("/")):
            return True
    return False


def _is_app_file(filename: str) -> bool:
    if not filename or filename.startswith("<"):
        return False
    path = os.path.abspath(filename)
    if path.startswith(_PACKAGE_DIR):
        return False
    if "site-packages" in path or "dist-packages" in path:
        return False
    return not any(path.startswith(d) for d in _STDLIB_DIRS)


def find_app_frame(skip: int = 1) -> Tuple[Optional[str], Optional[int]]:
    """File and line of the nearest caller that belongs to the application, not a library."""
    frame = sys._getframe(skip)
    while frame is not None:
        if _is_app_file(frame.f_code.co_filename):
            return frame.f_code.co_filename, frame.f_lineno
        frame = frame.f_back
    return None, None


def relative_path(filename: Optional[str]) -> Optional[str]:
    if not filename:
        return filename
    try:
        rel = os.path.relpath(filename)
        return filename if rel.startswith("..") else rel
    except ValueError:
        return filename


def line_preview(filename: str, lineno: int, around: int = 9) -> Dict[str, str]:
    lines = {}
    for n in range(max(1, lineno - around), lineno + around + 1):
        line = linecache.getline(filename, n)
        if not line and n > lineno:
            break
        lines[str(n)] = line.rstrip("\n")
    return lines


def format_exception(exc: BaseException) -> Dict[str, Any]:
    tb = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    frames = list(reversed(tb))
    if frames:
        origin = next((f for f in frames if _is_app_file(f.filename)), frames[0])
        file, line = origin.filename, origin.lineno or 0
    else:
        file, line = None, 0
    trace = [
        {"file": relative_path(f.filename), "line": f.lineno, "function": f.name, "code": (f.line or "").strip(),
         "vendor": not _is_app_file(f.filename)}
        for f in frames
    ]
    data: Dict[str, Any] = {
        "class": class_name(exc),
        "file": relative_path(file),
        "line": line,
        "message": str(exc) or class_name(exc),
        "trace": trace,
        "line_preview": line_preview(file, line) if file else {},
    }
    chain = exc.__cause__ or (exc.__context__ if not exc.__suppress_context__ else None)
    if chain is not None:
        data["previous"] = {"class": class_name(chain), "message": str(chain)}
    return data


def exception_context(exc: BaseException) -> Dict[str, Any]:
    ctx = {}
    for key, value in vars(exc).items() if hasattr(exc, "__dict__") else ():
        if not key.startswith("_"):
            ctx[key] = to_jsonable(value, depth=3)
    return ctx


def headers_to_dict(items: Iterable[Tuple[Any, Any]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, value in items:
        if isinstance(key, bytes):
            key = key.decode("latin-1")
        if isinstance(value, bytes):
            value = value.decode("latin-1")
        key = key.lower()
        out[key] = f"{out[key]}, {value}" if key in out else value
    return out


def parse_body(body: bytes, content_type: Optional[str]) -> Any:
    if not body:
        return {}
    content_type = (content_type or "").lower()
    try:
        if "json" in content_type:
            return json.loads(body.decode("utf-8"))
        if "application/x-www-form-urlencoded" in content_type:
            from urllib.parse import parse_qs

            parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
            return {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}
        if "multipart/form-data" in content_type:
            return parse_multipart(body, content_type)
        if content_type.startswith("text/") or "xml" in content_type:
            return body.decode("utf-8", "replace")
    except Exception:
        pass
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return f"<{len(body)} bytes binary>"


def parse_multipart(body: bytes, content_type: str) -> Dict[str, Any]:
    from email.parser import BytesParser
    from email.policy import HTTP

    message = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body)
    fields: Dict[str, Any] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            fields[name] = {"filename": filename, "size": len(payload), "type": part.get_content_type()}
        else:
            fields[name] = payload.decode("utf-8", "replace")
    return fields


def format_response_body(
    body: bytes, content_type: Optional[str], status: int, headers: Dict[str, str], size_limit_kb: int,
    truncated: bool = False,
) -> Any:
    content_type = (content_type or "").lower()
    if 300 <= status < 400 and headers.get("location"):
        return f"Redirected to {headers['location']}"
    if "text/event-stream" in content_type:
        return "Streamed Response"
    if truncated or len(body) > size_limit_kb * 1024:
        return "Purged By Tracefox"
    if not body:
        return "Empty Response"
    if "html" in content_type:
        return "HTML Response"
    if "json" in content_type:
        try:
            return json.loads(body.decode("utf-8"))
        except Exception:
            pass
    if content_type.startswith("text/") or "xml" in content_type or "javascript" in content_type:
        return body.decode("utf-8", "replace")
    if content_type and not content_type.startswith(("application/x-www-form", "text/")):
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError:
            return "Binary Response"
    return body.decode("utf-8", "replace")


def memory_usage_mb() -> Optional[float]:
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(usage / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)
    except Exception:
        return None


def ensure_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]
