from django.core.management.base import BaseCommand

from tracefox.core import tracefox


class Command(BaseCommand):
    help = "Delete all Tracefox data"

    def handle(self, *args, **options):
        tracefox.clear()
        self.stdout.write("Tracefox entries cleared!")
