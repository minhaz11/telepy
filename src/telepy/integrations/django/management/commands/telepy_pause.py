from django.core.management.base import BaseCommand

from telepy.core import telepy


class Command(BaseCommand):
    help = "Pause Telepy recording"

    def handle(self, *args, **options):
        telepy.pause_recording()
        self.stdout.write("Telepy recording paused.")
