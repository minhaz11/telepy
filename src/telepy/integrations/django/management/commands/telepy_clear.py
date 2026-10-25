from django.core.management.base import BaseCommand

from telepy.core import telepy


class Command(BaseCommand):
    help = "Delete all Telepy data"

    def handle(self, *args, **options):
        telepy.clear()
        self.stdout.write("Telepy entries cleared!")
