from django.core.management.base import BaseCommand

from telepy.core import telepy


class Command(BaseCommand):
    help = "Resume Telepy recording"

    def handle(self, *args, **options):
        telepy.resume_recording()
        self.stdout.write("Telepy recording resumed.")
