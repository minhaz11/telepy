from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from .core import telepy


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="telepy", description="Manage Telepy entries.")
    parser.add_argument("--database-url", help="Telepy database URL (default: $TELEPY_DATABASE_URL)")
    sub = parser.add_subparsers(dest="command", required=True)
    prune = sub.add_parser("prune", help="Delete entries older than --hours")
    prune.add_argument("--hours", type=int, default=24)
    prune.add_argument("--keep-exceptions", action="store_true")
    sub.add_parser("clear", help="Delete all entries")
    sub.add_parser("pause", help="Pause recording")
    sub.add_parser("resume", help="Resume recording")
    sub.add_parser("migrate", help="Create the Telepy tables")
    args = parser.parse_args(argv)

    if args.database_url:
        telepy.configure(database_url=args.database_url)
    if args.command == "prune":
        print(f"{telepy.prune(args.hours, args.keep_exceptions)} entries pruned.")
    elif args.command == "clear":
        telepy.clear()
        print("Telepy entries cleared!")
    elif args.command == "pause":
        telepy.pause_recording()
        print("Telepy recording paused.")
    elif args.command == "resume":
        telepy.resume_recording()
        print("Telepy recording resumed.")
    elif args.command == "migrate":
        telepy.storage.migrate()
        print("Telepy tables are ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
