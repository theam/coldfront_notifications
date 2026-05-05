"""
Seed SenderConfig from ColdFront email settings.

Run once after installation:
    python manage.py seed_notification_senders

Safe to re-run — skips addresses that already exist.
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from coldfront_notifications.models import SenderConfig

SETTINGS_MAP = [
    ("EMAIL_SENDER",                "Default Sender",  True,  False),
    ("EMAIL_TICKET_SYSTEM_ADDRESS", "Ticket System",   False, True),
    ("EMAIL_DIRECTOR_EMAIL_ADDRESS","Director",         False, False),
]


class Command(BaseCommand):
    help = "Create SenderConfig entries from EMAIL_SENDER, EMAIL_TICKET_SYSTEM_ADDRESS, EMAIL_DIRECTOR_EMAIL_ADDRESS"

    def handle(self, *args, **options):
        created = 0
        skipped = 0
        for setting_name, label, is_default, is_reply_to in SETTINGS_MAP:
            email = getattr(settings, setting_name, None)
            if not email:
                self.stdout.write(f"  {setting_name}: not set, skipping")
                continue
            _, was_created = SenderConfig.objects.get_or_create(
                email=email,
                defaults={
                    "label": label,
                    "is_default": is_default,
                    "is_reply_to": is_reply_to,
                },
            )
            if was_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(f"  {setting_name}: created {email}"))
            else:
                skipped += 1
                self.stdout.write(f"  {setting_name}: {email} already exists, skipped")

        self.stdout.write(self.style.SUCCESS(
            f"\nDone: {created} created, {skipped} skipped"
        ))
