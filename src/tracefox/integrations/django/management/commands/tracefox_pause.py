from django.core.management.base import BaseCommand

from tracefox.core import tracefox


class Command(BaseCommand):
    help = "Pause Tracefox recording"

    def handle(self, *args, **options):
        tracefox.pause_recording()
        self.stdout.write("Tracefox recording paused.")
