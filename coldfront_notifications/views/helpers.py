"""Shared view helpers: mixins and utility functions."""
import logging
import threading

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import close_old_connections

from ..campaign_sender import send_notification_campaign

logger = logging.getLogger(__name__)


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Restrict access to staff users, following ColdFront conventions."""

    def test_func(self) -> bool:
        if not self.request.user.is_staff:
            messages.error(
                self.request,
                "You do not have permission to view the previous page.",
            )
            return False
        return True


def dispatch_send(campaign_pk: int) -> None:
    """Queue the send task on Celery.

    Falls back to a background thread when the broker is unreachable so
    the HTTP response returns immediately.
    """
    try:
        send_notification_campaign.delay(campaign_pk)
    except Exception as exc:
        logger.warning(
            "Celery broker unreachable (%s) — sending in background thread",
            exc,
        )

        def _run() -> None:
            try:
                close_old_connections()
                send_notification_campaign(campaign_pk)
            except Exception as thread_exc:
                logger.error(
                    "Background send thread crashed for campaign %s: %s",
                    campaign_pk,
                    thread_exc,
                    exc_info=True,
                )

        threading.Thread(target=_run, daemon=True).start()
