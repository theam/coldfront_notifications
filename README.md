# coldfront-notifications

A [ColdFront](https://coldfront.readthedocs.io/) plugin for sending bulk email notifications to targeted user groups. Designed for HPC centers running ColdFront.

## Features

### Compose & Send
- **Cascading recipient filters** — filter by Department, Project, Resource, Allocation Status, Allocation, and User Role. All filter data loads once on page load; cross-filter narrowing happens entirely client-side with zero AJAX round-trips. See [FILTERS.md](FILTERS.md) for the full architecture.
- **Bidirectional cascade** — top-down (department narrows projects, resources, allocations) and bottom-up (selecting a role auto-selects matching departments).
- **Template-based composition** — pick a saved template or write a one-off. Templates support `{{variable}}` placeholders that resolve per recipient.
- **Live email preview** — preview the rendered email for sample recipients before sending. A dropdown lets you switch between recipients to see how variable substitution looks for each.
- **Per-user dedupe control** — users matched on multiple projects/allocations can receive one email per context or be deduped to one. Controlled per-user in the recipient preview with a "Dedupe all" option.
- **Pre-flight validation** — checks every `{{token}}` resolves for every recipient before enabling Send. Unknown tokens, empty values, and resolution errors are surfaced with clear messages.
- **Draft auto-save** — compose progress is auto-saved every 30 seconds. Drafts are visible in the notifications list and can be resumed at any time. Navigate away without losing work.
- **Per-email delivery** — each recipient gets an individually addressed email with their specific variable substitution. No bulk BCC.

### Template Variables
- **Query variables** — auto-resolved per recipient from ColdFront data. Built-in resolver paths covering Recipient, Project, PI, Department, Role, Allocation, and Resource fields.
- **Manual variables** — fixed values defined once at creation time and reused across all notifications (e.g. a maintenance date, a renewal URL).
- **Full CRUD UI** — create, edit, soft-delete variables from the plugin's own interface. Query variables pick from a whitelisted dropdown; manual variables store a typed value.

### Notifications
- **Dashboard** — stat cards (Total Delivered, Sending Now, Failed 30d, Total Notifications) plus a recent notifications table.
- **Notification detail** — live delivery progress (AJAX polling), per-recipient log with status badges, and a view icon showing the exact rendered email as delivered.
- **Notification list** — status filter tabs (All, Sent, Partial, Failed, Draft) with Edit action on drafts.
- **Resend failed** — one-click resend to failed recipients.

### Settings
- **Sender & Reply-To management** — configure available From/Reply-To addresses with default flags.

### Infrastructure
- **Batched delivery** — sends in configurable batches with delay between them to avoid SMTP rate limits. Single SMTP connection reused across all emails.
- **Retry on transient errors** — 4xx SMTP errors and disconnects are retried with exponential backoff.
- **Celery-optional** — queues via Celery when a broker is available; falls back to a background thread for environments without a worker.
- **Django messages** — success/error/warning banners for every user action.

---

## Installation

### From source (production)

```bash
pip install "git+https://github.com/theam/coldfront_notifications.git"
```

To install from a specific branch:

```bash
pip install "git+https://github.com/theam/coldfront_notifications.git@<branch-name>"
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

# Templates and static files (adjust the path to your install location)
TEMPLATES[0]['DIRS'] += [
    '/usr/src/app/coldfront_notifications/coldfront_notifications/templates',
]
STATICFILES_DIRS += [
    '/usr/src/app/coldfront_notifications/coldfront_notifications/static',
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

### Add the navbar dropdown

Add this line to your ColdFront `authorized_navbar.html` (typically in `templates/common/authorized_navbar.html`), inside the `<ul>` that holds the nav items:

```django
{% include "coldfront_notifications/navbar.html" %}
```

The dropdown is only visible to staff/superusers.

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

Or create your own variables from the UI: **Notifications > Template Variables > New Variable**.

### Configure sender addresses

If your ColdFront instance has `EMAIL_SENDER`, `EMAIL_TICKET_SYSTEM_ADDRESS`, or `EMAIL_DIRECTOR_EMAIL_ADDRESS` in settings, seed them in one command:

```bash
python manage.py seed_notification_senders
```

Safe to re-run — skips addresses that already exist. You can also manage addresses manually at **Notifications > Settings**.

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

## Module Structure

| Module | Purpose |
|---|---|
| `models.py` | NotificationCampaign, NotificationLog, NotificationTemplate, NotificationVariable, SenderConfig |
| `filters.py` | FilterDataBuilder, RecipientResolver, 6 filter classes |
| `template_variable_value_resolver.py` | ResolverRegistry, resolver groups, ResolverContext |
| `notification_validator.py` | NotificationValidator, extract_tokens, determine_scope |
| `campaign_sender.py` | CampaignSender, TemplateRenderer, SmtpDelivery, Celery task |
| `views/compose.py` | ComposeView, RecipientCountView, PreviewRenderView, ValidateView, DraftSaveView |
| `views/campaigns.py` | CampaignListView, CampaignDetailView, ResendFailedView, ResendComposeView |
| `views/templates.py` | TemplateListView, TemplateFormView, TemplateDeleteView, TemplateJsonView |
| `views/settings.py` | VariableListView, VariableFormView, SettingsView, SenderFormView |
| `views/helpers.py` | StaffRequiredMixin, dispatch_send |

---

## Navigation

| Menu item | Path | Description |
|---|---|---|
| Dashboard | `/notifications/` | Stats + recent notifications |
| Notifications | `/notifications/campaigns/` | Full list with status filter (incl. Draft) |
| Compose Notification | `/notifications/compose/` | Build and send (or `?draft=<pk>` to resume) |
| Templates | `/notifications/templates/` | Create/edit/delete templates |
| Template Variables | `/notifications/variables/` | Create/edit/delete `{{token}}` definitions |
| Settings | `/notifications/settings/` | Sender/reply-to addresses |

---

## License

GPL-3.0
