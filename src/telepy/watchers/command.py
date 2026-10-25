from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Dict, Iterator

from ..entry import EntryType, IncomingEntry
from ..utils import to_jsonable
from .base import Watcher, safely

if TYPE_CHECKING:
    from ..core import Telepy


class CommandWatcher(Watcher):
    """Console commands: Click / Typer / Flask CLI, Django management commands, or ``telepy.command()``."""

    name = "commands"

    def register(self) -> None:
        self.when_imported("click", self._register_click)
        self.when_imported("django.core.management.base", self._register_django)

    def ignored(self, *names: str) -> bool:
        ignore = set(self.options.get("ignore", []))
        return any(n in ignore for n in names if n)

    @safely
    def record(self, name: str, exit_code: int, arguments: Dict[str, Any], options: Dict[str, Any]) -> None:
        if not self.active:
            return
        entry = IncomingEntry(EntryType.COMMAND, {
            "command": name,
            "exit_code": exit_code,
            "arguments": to_jsonable(arguments, depth=4),
            "options": to_jsonable(options, depth=4),
        })
        if exit_code:
            entry.add_tags(["failed"])
        self.telepy.record(entry)

    def _register_click(self, click: Any) -> None:
        watcher = self

        def make(original):
            def invoke(self, ctx):
                if isinstance(self, click.Group):
                    return original(self, ctx)
                name = ctx.command_path or ctx.info_name or self.name
                if watcher.ignored(name, ctx.info_name, self.name) or not watcher.active:
                    return original(self, ctx)
                arguments = {p.name: ctx.params.get(p.name) for p in self.params if isinstance(p, click.Argument)}
                options = {k: v for k, v in ctx.params.items() if k not in arguments}
                with record_command(watcher.telepy, name, arguments, options):
                    return original(self, ctx)
            return invoke

        self.patch(click.Command, "invoke", make)

    def _register_django(self, base: Any) -> None:
        watcher = self

        def make(original):
            def execute(self, *args, **options):
                name = type(self).__module__.rsplit(".", 1)[-1]
                if watcher.ignored(name) or not watcher.active:
                    return original(self, *args, **options)
                clean = {k: v for k, v in options.items() if k not in ("stdout", "stderr")}
                with record_command(watcher.telepy, name, {"args": list(args)}, clean):
                    return original(self, *args, **options)
            return execute

        self.patch(base.BaseCommand, "execute", make)


@contextmanager
def record_command(telepy: "Telepy", name: str, arguments: Dict[str, Any], options: Dict[str, Any]) -> Iterator[None]:
    watcher = telepy.watcher("commands")
    if watcher is None or not watcher.active:
        yield
        return
    with telepy.batch():
        exit_code = 0
        try:
            yield
        except SystemExit as exc:
            exit_code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
            raise
        except BaseException as exc:
            exit_code = getattr(exc, "exit_code", None) or getattr(exc, "returncode", None) or 1
            if not isinstance(exit_code, int):
                exit_code = 1
            if type(exc).__name__ == "Exit" and getattr(exc, "exit_code", None) == 0:
                exit_code = 0
            elif telepy.watcher("exceptions") is not None and not isinstance(exc, KeyboardInterrupt):
                telepy.watcher("exceptions").record_exception(exc)
            raise
        finally:
            watcher.record(name, exit_code, arguments, options)
