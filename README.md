# coldfront-notifications

A [ColdFront](https://coldfront.readthedocs.io/) plugin for sending bulk email notifications to targeted user groups. Designed for HPC centers running ColdFront.

## Features

### Compose & Send
- **Cascading recipient filters** — filter by Project, Department, Allocation, Resource, Allocation Status, and User Role. Each filter narrows the downstream options in real time.
- **Template-based composition** — pick a saved template or write a one-off. Templates support `{{variable}}` placeholders that resolve per recipient.
- **Live email preview** — preview the rendered email for sample recipients before sending. A dropdown lets you switch between recipients to see how variable substitution looks for each.
- **Per-user dedupe control** — users matched on multiple projects/allocations can receive one email per context or be deduped to one. Controlled per-user in the recipient preview with a "Dedupe all" option.
- **Pre-flight validation** — checks every `{{token}}` resolves for every recipient before enabling Send. Unknown tokens, empty values, and resolution errors are surfaced with clear messages.
- **Per-email delivery** — each recipient gets an individually addressed email with their specific variable substitution. No bulk BCC.

### Template Variables
- **Query variables** — auto-resolved per recipient from ColdFront data. 28 built-in resolver paths covering Recipient, Project, PI, Department, Role, Allocation, and Resource fields.
- **Manual variables** — fixed values defined once at creation time and reused across all notifications (e.g. a maintenance date, a renewal URL).
- **Full CRUD UI** — create, edit, soft-delete variables from the plugin's own interface. Query variables pick from a whitelisted dropdown; manual variables store a typed value.

### Notifications
- **Dashboard** — stat cards (Total Delivered, Sending Now, Failed 30d, Total Notifications) plus a recent notifications table.
- **Notification detail** — live delivery progress (AJAX polling), per-recipient log with status badges, and a view icon showing the exact rendered email as delivered.
- **Resend failed** — one-click resend to failed recipients.
- **Soft delete** — deleted templates and variables remain visible (crossed out) on past notification details.

### Settings
- **Sender & Reply-To management** — configure available From/Reply-To addresses with default flags.

### Infrastructure
- **Batched delivery** — sends in configurable batches with delay between them to avoid SMTP rate limits. Single SMTP connection reused across all emails.
- **Retry on transient errors** — 4xx SMTP errors and disconnects are retried with exponential backoff.
- **Celery-optional** — queues via Celery when a broker is available; falls back to a background thread for environments without a worker.
- **Django messages** — success/error/warning banners for every user action.

---

## Installation

### From PyPI

```bash
pip install coldfront-notifications
```

With Celery support:

```bash
pip install coldfront-notifications[celery]
```

### From source (development)

```bash
git clone https://github.com/theam/coldfront_notifications.git
cd coldfront-notifications
pip install -e .
```

### Enable the plugin

Add to your ColdFront `local_settings.py`:

```python
INSTALLED_APPS += ["coldfront_notifications"]

EXTRA_APPS_URLS = [
    ("notifications/", "coldfront_notifications.urls", "notifications"),
]
```

If your ColdFront instance doesn't already wire `EXTRA_APPS_URLS`, add this to your main `urls.py`:

```python
from django.conf import settings
from django.urls import path, include

for url_prefix, module, namespace in getattr(settings, "EXTRA_APPS_URLS", []):
    urlpatterns += [path(url_prefix, include((module, namespace)))]
```

### Run migrations

```bash
python manage.py migrate coldfront_notifications
```

### Seed the default Template Variables catalog (optional)

```bash
python manage.py shell -c "
import json
from coldfront_notifications.models import NotificationVariable
with open('coldfront_notifications/coldfront_notifications/fixtures/variables.json') as f:
    for row in json.load(f):
        fields = row['fields']
        NotificationVariable.objects.update_or_create(
            key=fields['key'],
            defaults={k: v for k, v in fields.items() if k != 'key'},
        )
"
```

Or create your own variables from the UI: **Notifications → Template Variables → New Variable**.

### Configure sender addresses

Go to **Notifications → Settings** and add your sender/reply-to addresses. Flag one as Default From and one as Default Reply-To.

---

## Configuration

All settings are optional. Defaults work out of the box. Override in `local_settings.py`:

```python
# Email dispatch
NOTIFICATION_BATCH_SIZE          = 50    # emails per SMTP batch
NOTIFICATION_BATCH_DELAY         = 2.0   # seconds between batches
NOTIFICATION_MAX_RETRIES         = 3     # retries per email on transient SMTP errors
NOTIFICATION_RETRY_DELAY         = 1.0   # initial retry delay (doubles each retry)

# UI
NOTIFICATION_PREVIEW_PAGE_SIZE   = 50    # rows per page in recipient preview
NOTIFICATION_PREVIEW_RENDER_LIMIT = 10   # sample recipients in email preview modal
```

---

## Testing Email with MailHog

[MailHog](https://github.com/mailhog/MailHog) catches all outbound email and provides a web UI — no emails actually delivered.

### Docker setup

```yaml
# docker-compose.yml
services:
  mailhog:
    image: mailhog/mailhog:latest
    ports:
      - "127.0.0.1:8025:8025"
```

```bash
docker compose up -d mailhog
```

Add to `local_settings.py`:

```python
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'mailhog'  # or 'localhost' if not using Docker networking
EMAIL_PORT = 1025
EMAIL_USE_TLS = False
```

Open `http://127.0.0.1:8025` to see sent emails.

### Switching to production SMTP

```python
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.example.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'your-username'
EMAIL_HOST_PASSWORD = 'your-password'
```

No code changes — the SMTP path is identical.

---

## Navigation

| Menu item | Path | Description |
|---|---|---|
| Dashboard | `/notifications/` | Stats + recent notifications |
| Notifications | `/notifications/campaigns/` | Full list with status filter |
| Compose Notification | `/notifications/compose/` | Build and send |
| Templates | `/notifications/templates/` | Create/edit/delete templates |
| Template Variables | `/notifications/variables/` | Create/edit/delete `{{token}}` definitions |
| Settings | `/notifications/settings/` | Sender/reply-to addresses |

---

## License

GPL-3.0