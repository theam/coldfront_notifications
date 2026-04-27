"""
Plugin settings with defaults.

Every constant can be overridden in Django's settings (e.g. local_settings.py)
by defining the same name. The plugin always reads from this module — never
directly from django.conf.settings — so there's one source of truth for
defaults and one import path for consumers.

Usage inside the plugin:
    from coldfront_notifications.conf import BATCH_SIZE, PREVIEW_PAGE_SIZE
"""
from django.conf import settings

# ── Email dispatch ────────────────────────────────────────────────
BATCH_SIZE   = getattr(settings, "NOTIFICATION_BATCH_SIZE",  50)
BATCH_DELAY  = getattr(settings, "NOTIFICATION_BATCH_DELAY", 2.0)   # seconds
MAX_RETRIES  = getattr(settings, "NOTIFICATION_MAX_RETRIES", 3)
RETRY_DELAY  = getattr(settings, "NOTIFICATION_RETRY_DELAY", 1.0)   # seconds, doubles each retry

# ── Recipient preview ────────────────────────────────────────────
PREVIEW_PAGE_SIZE = getattr(settings, "NOTIFICATION_PREVIEW_PAGE_SIZE", 50)

# ── Preview render (email preview modal) ─────────────────────────
PREVIEW_RENDER_LIMIT = getattr(settings, "NOTIFICATION_PREVIEW_RENDER_LIMIT", 10)
