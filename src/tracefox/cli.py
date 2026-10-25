from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from .core import tracefox


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="tracefox", description="Manage Tracefox entries.")
    parser.add_argument("--database-url", help="Tracefox database URL (default: $TRACEFOX_DATABASE_URL)")
    sub = parser.add_subparsers(dest="command", required=True)
    prune = sub.add_parser("prune", help="Delete entries older than --hours")
    prune.add_argument("--hours", type=int, default=24)
    prune.add_argument("--keep-exceptions", action="store_true")
    sub.add_parser("clear", help="Delete all entries")
    sub.add_parser("pause", help="Pause recording")
    sub.add_parser("resume", help="Resume recording")
    sub.add_parser("migrate", help="Create the Tracefox tables")
    args = parser.parse_args(argv)

    if args.database_url:
        tracefox.configure(database_url=args.database_url)
    if args.command == "prune":
        print(f"{tracefox.prune(args.hours, args.keep_exceptions)} entries pruned.")
    elif args.command == "clear":
        tracefox.clear()
        print("Tracefox entries cleared!")
    elif args.command == "pause":
        tracefox.pause_recording()
        print("Tracefox recording paused.")
    elif args.command == "resume":
        tracefox.resume_recording()
        print("Tracefox recording resumed.")
    elif args.command == "migrate":
        tracefox.storage.migrate()
        print("Tracefox tables are ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
