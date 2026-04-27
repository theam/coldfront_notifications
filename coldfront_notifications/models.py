from django.core.exceptions import ValidationError
from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class NotificationVariable(models.Model):
    """
    Catalog of template variables admins can use in {{tokens}}.

    source=query   → value auto-resolved from recipient context (Project,
                     Allocation, etc.) using a whitelisted resolver_key.
    source=manual  → admin types a value at compose time; stored on
                     NotificationCampaign.extra_context.
    """
    SOURCE_QUERY  = "query"
    SOURCE_MANUAL = "manual"
    SOURCE_CHOICES = [
        (SOURCE_QUERY,  "Query (auto-resolved from data)"),
        (SOURCE_MANUAL, "Manual (admin enters per-campaign)"),
    ]

    WIDGET_TEXT     = "text"
    WIDGET_TEXTAREA = "textarea"
    WIDGET_DATE     = "date"
    WIDGET_DATETIME = "datetime"
    WIDGET_URL      = "url"
    WIDGET_CHOICES = [
        (WIDGET_TEXT,     "Text"),
        (WIDGET_TEXTAREA, "Multi-line text"),
        (WIDGET_DATE,     "Date"),
        (WIDGET_DATETIME, "Date & Time"),
        (WIDGET_URL,      "URL"),
    ]

    key          = models.SlugField(
        max_length=60, unique=True,
        help_text="Token used in {{key}} placeholders (letters, digits, _)",
    )
    label        = models.CharField(max_length=120)
    description  = models.CharField(max_length=255, blank=True)
    example      = models.CharField(max_length=255, blank=True)
    source       = models.CharField(max_length=10, choices=SOURCE_CHOICES)
    resolver_key = models.CharField(
        max_length=80, blank=True,
        help_text="Required when source=query; must match a registered resolver",
    )
    input_widget = models.CharField(
        max_length=10, choices=WIDGET_CHOICES, default=WIDGET_TEXT,
        help_text="Input type for the Value field when source=manual",
    )
    value        = models.TextField(
        blank=True, default="",
        help_text="Fixed value used for every campaign (manual variables only).",
    )
    is_required  = models.BooleanField(default=True)
    is_deleted   = models.BooleanField(default=False)
    created      = models.DateTimeField(auto_now_add=True)
    modified     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return f"{{{{{self.key}}}}} ({self.get_source_display()})"

    def clean(self):
        from .resolvers import QUERY_RESOLVERS
        if self.source == self.SOURCE_QUERY:
            if not self.resolver_key:
                raise ValidationError({"resolver_key": "Required when source is Query."})
            if self.resolver_key not in QUERY_RESOLVERS:
                raise ValidationError({
                    "resolver_key":
                    f"'{self.resolver_key}' is not a registered resolver.",
                })
            # Query vars don't carry a stored value.
            self.value = ""
        else:
            self.resolver_key = ""
            if self.is_required and not (self.value or "").strip():
                raise ValidationError({
                    "value": "Required when source is Manual and 'Is required' is on.",
                })

    @property
    def required_scope(self):
        """Which context element the resolver depends on ('user', 'project', 'allocation', or None)."""
        from .resolvers import QUERY_SCOPES
        if self.source == self.SOURCE_MANUAL:
            return None
        return QUERY_SCOPES.get(self.resolver_key)


class SenderConfig(models.Model):
    label = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    is_default = models.BooleanField(default=False)
    is_reply_to = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.label} <{self.email}>"

    class Meta:
        ordering = ["-is_default", "label"]


class NotificationTemplate(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    subject = models.CharField(max_length=500)
    body = models.TextField()
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    @property
    def variables(self):
        """Extract {{variable}} placeholders from subject + body."""
        import re
        pattern = re.compile(r"\{\{(\w+)\}\}")
        found = pattern.findall(self.subject) + pattern.findall(self.body)
        # deduplicate preserving order
        seen = set()
        return [v for v in found if not (v in seen or seen.add(v))]

    @property
    def use_count(self):
        return self.campaigns.count()

    class Meta:
        ordering = ["name"]


class NotificationCampaign(models.Model):
    STATUS_QUEUED = "queued"
    STATUS_SENDING = "sending"
    STATUS_SENT = "sent"
    STATUS_PARTIAL = "partial"
    STATUS_FAILED = "failed"
    STATUS_DRAFT = "draft"
    STATUS_CHOICES = [
        (STATUS_QUEUED,   "Queued"),
        (STATUS_SENDING,  "Sending"),
        (STATUS_SENT,     "Sent"),
        (STATUS_PARTIAL,  "Partial"),
        (STATUS_FAILED,   "Failed"),
        (STATUS_DRAFT,    "Draft"),
    ]

    template = models.ForeignKey(
        NotificationTemplate,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="campaigns",
    )
    subject = models.CharField(max_length=500)
    body = models.TextField()
    sender = models.EmailField()
    reply_to = models.EmailField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)

    # filters snapshot (JSON)
    filters_snapshot = models.JSONField(default=dict, blank=True)

    # Admin-supplied values for source=manual variables ({"maint_date": "...", ...})
    extra_context    = models.JSONField(default=dict, blank=True)

    recipient_count = models.PositiveIntegerField(default=0)
    delivered_count = models.PositiveIntegerField(default=0)
    failed_count    = models.PositiveIntegerField(default=0)

    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="notification_campaigns",
    )
    sent_at      = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.subject

    @property
    def duration(self):
        if not self.sent_at or not self.completed_at:
            return "—"
        delta = self.completed_at - self.sent_at
        total = int(delta.total_seconds())
        if total < 60:
            return f"{total}s"
        mins, secs = divmod(total, 60)
        if mins < 60:
            return f"{mins}m {secs}s"
        hours, mins = divmod(mins, 60)
        return f"{hours}h {mins}m {secs}s"

    @property
    def delivery_pct(self):
        if not self.recipient_count:
            return 0
        return round(self.delivered_count / self.recipient_count * 100, 1)

    @property
    def filter_summary(self):
        """Return list of human-readable filter badge strings."""
        labels = {
            "projects":     "Project",
            "allocations":  "Allocation",
            "departments":  "Department",
            "resources":    "Resource",
            "statuses":     "Status",
            "roles":        "Role",
        }
        badges = []
        for key, label in labels.items():
            vals = self.filters_snapshot.get(key, [])
            if vals:
                badges.append(f"{label}: {', '.join(vals)}")
        return badges

    class Meta:
        ordering = ["-created_at"]


class NotificationLog(models.Model):
    STATUS_QUEUED    = "queued"
    STATUS_DELIVERED = "delivered"
    STATUS_BOUNCED   = "bounced"
    STATUS_FAILED    = "failed"
    STATUS_CHOICES = [
        (STATUS_QUEUED,    "Queued"),
        (STATUS_DELIVERED, "Delivered"),
        (STATUS_BOUNCED,   "Bounced"),
        (STATUS_FAILED,    "Failed"),
    ]

    campaign  = models.ForeignKey(
        NotificationCampaign,
        on_delete=models.CASCADE,
        related_name="logs",
    )
    email     = models.EmailField()
    status    = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    timestamp = models.DateTimeField(auto_now_add=True)
    notes     = models.TextField(blank=True)
    rendered_subject = models.TextField(blank=True, default="")
    rendered_body    = models.TextField(blank=True, default="")
    # Per-email context (one log row = one sent email)
    project_id    = models.IntegerField(null=True, blank=True, db_index=True)
    allocation_id = models.IntegerField(null=True, blank=True, db_index=True)

    def __str__(self):
        return f"{self.email} — {self.status}"

    class Meta:
        ordering = ["timestamp"]
