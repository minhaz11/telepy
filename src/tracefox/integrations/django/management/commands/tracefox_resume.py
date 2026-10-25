from django.core.management.base import BaseCommand

from tracefox.core import tracefox


class Command(BaseCommand):
    help = "Resume Tracefox recording"

    def handle(self, *args, **options):
        tracefox.resume_recording()
        self.stdout.write("Tracefox recording resumed.")
