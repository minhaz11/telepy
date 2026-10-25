from django.core.management.base import BaseCommand

from telepy.core import telepy


class Command(BaseCommand):
    help = "Prune stale entries from the Telepy database"

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=24, help="Delete entries older than this many hours")
        parser.add_argument("--keep-exceptions", action="store_true", help="Keep exception entries")

    def handle(self, *args, **options):
        count = telepy.prune(options["hours"], options["keep_exceptions"])
        self.stdout.write(f"{count} entries pruned.")
