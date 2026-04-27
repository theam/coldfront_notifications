"""
Celery tasks for async email dispatch.

Sends one email per (user, project, allocation) tuple (scope permitting).
Hard-fails the campaign if any token cannot be resolved for any recipient.

Batch handling:
  - Reuses a single SMTP connection across all emails
  - Sends in batches with a configurable delay between them
  - Retries transient SMTP errors (4xx) with exponential backoff

Settings (in Django settings / local_settings.py):
  NOTIFICATION_BATCH_SIZE   = 50     # emails per batch
  NOTIFICATION_BATCH_DELAY  = 2.0    # seconds between batches
  NOTIFICATION_MAX_RETRIES  = 3      # retries per email on transient failure
  NOTIFICATION_RETRY_DELAY  = 1.0    # initial retry delay (doubles each retry)
"""
import logging
import re
import time
import smtplib

from django.core.mail import EmailMessage, get_connection
from django.utils import timezone

from .conf import BATCH_SIZE, BATCH_DELAY, MAX_RETRIES, RETRY_DELAY

logger = logging.getLogger(__name__)

try:
    from celery import shared_task
except ImportError:
    def shared_task(fn):
        return fn


TOKEN_RE = re.compile(r"\{\{(\w+)\}\}")


def _render(template_str: str, values: dict) -> str:
    def repl(m):
        k = m.group(1)
        return values[k] if k in values else m.group(0)
    return TOKEN_RE.sub(repl, template_str or "")


def _build_values(tokens, vars_by_key, ctx):
    from .models import NotificationVariable
    from .resolvers import MissingValue, resolve

    out = {}
    for t in tokens:
        v = vars_by_key.get(t)
        if v is None:
            raise MissingValue(f"unknown token {t!r}")
        if v.source == NotificationVariable.SOURCE_MANUAL:
            val = (v.value or "").strip()
            if not val and v.is_required:
                raise MissingValue(f"manual variable {t!r} has no stored value")
            out[t] = v.value or ""
        else:
            try:
                out[t] = resolve(v.resolver_key, ctx)
            except MissingValue:
                raise MissingValue(
                    f"cannot resolve {v.resolver_key} for {ctx['user'].email}"
                )
    return out


def _is_transient(exc):
    """Return True if the SMTP error is transient (4xx) and worth retrying."""
    if isinstance(exc, smtplib.SMTPResponseException):
        return 400 <= exc.smtp_code < 500
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return True
    if isinstance(exc, ConnectionError):
        return True
    return False


def _send_with_retry(msg, connection, max_retries=MAX_RETRIES, base_delay=RETRY_DELAY):
    """
    Send an EmailMessage with retry on transient SMTP errors.
    Re-opens the connection on disconnect.
    Returns None on success or the exception on permanent failure.
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            msg.connection = connection
            msg.send()
            return None
        except Exception as exc:
            last_exc = exc
            if not _is_transient(exc) or attempt == max_retries:
                return exc
            delay = base_delay * (2 ** attempt)
            logger.warning(
                "Transient SMTP error (attempt %d/%d), retrying in %.1fs: %s",
                attempt + 1, max_retries + 1, delay, exc,
            )
            time.sleep(delay)
            # Re-open connection if it was dropped
            try:
                connection.close()
            except Exception:
                pass
            try:
                connection.open()
            except Exception as reopen_exc:
                logger.error("Failed to re-open SMTP connection: %s", reopen_exc)
                return reopen_exc
    return last_exc


@shared_task
def send_notification_campaign(campaign_pk: int):
    from .models import NotificationCampaign, NotificationLog, NotificationVariable
    from .resolvers import MissingValue
    from .utils import enumerate_recipients_deduped
    from .validators import _extract_tokens, _required_scope

    try:
        campaign = NotificationCampaign.objects.get(pk=campaign_pk)
    except NotificationCampaign.DoesNotExist:
        logger.error("Campaign %s not found", campaign_pk)
        return

    campaign.status  = NotificationCampaign.STATUS_SENDING
    campaign.sent_at = timezone.now()
    campaign.save(update_fields=["status", "sent_at"])

    campaign.logs.all().delete()

    tokens = _extract_tokens(campaign.subject, campaign.body)
    vars_by_key = {v.key: v for v in NotificationVariable.objects.filter(key__in=tokens)}

    unknown = [t for t in tokens if t not in vars_by_key]
    if unknown:
        _fail_campaign(campaign, f"Unknown tokens: {', '.join(unknown)}")
        return

    scope = _required_scope(list(vars_by_key.values()))
    snapshot = campaign.filters_snapshot or {}
    dedupe_users = snapshot.get("dedupe_users") or []

    # --- Phase 1: resolve all variables (streaming, no full list in memory) ---
    # We resolve per-tuple and yield. Hard-fail on first resolution error.
    tuples = enumerate_recipients_deduped(snapshot, scope, dedupe_users)
    bcc_pool = list(set(
        a.strip() for a in snapshot.get("extra_recipients", []) if a.strip()
    ))

    # Pre-check: collect resolved emails into a generator that hard-fails.
    # We need to know the total count upfront for progress, so we do a
    # two-pass approach: resolve all first, then send in batches.
    resolved = []
    for user, project, allocation in tuples:
        ctx = {"user": user, "project": project, "allocation": allocation}
        try:
            values = _build_values(tokens, vars_by_key, ctx)
        except MissingValue as exc:
            _fail_campaign(campaign, f"Resolution error: {exc}")
            return
        resolved.append((
            user, project, allocation,
            _render(campaign.subject, values),
            _render(campaign.body, values),
        ))

    if not resolved:
        _fail_campaign(campaign, "No recipients matched the filters.")
        return

    total = len(resolved)
    campaign.recipient_count = total
    campaign.save(update_fields=["recipient_count"])

    logger.info(
        "Campaign %s: %d emails to send (batch_size=%d, delay=%.1fs)",
        campaign_pk, total, BATCH_SIZE, BATCH_DELAY,
    )

    # --- Phase 2: send in batches over a single SMTP connection ---
    delivered = 0
    failed    = 0

    connection = get_connection()
    try:
        connection.open()
    except Exception as exc:
        _fail_campaign(campaign, f"Cannot open SMTP connection: {exc}")
        return

    try:
        for i, (user, project, allocation, subj, body) in enumerate(resolved):
            log = NotificationLog.objects.create(
                campaign=campaign,
                email=user.email,
                status=NotificationLog.STATUS_QUEUED,
                rendered_subject=subj,
                rendered_body=body,
                project_id=project.pk if project else None,
                allocation_id=allocation.pk if allocation else None,
            )

            msg = EmailMessage(
                subject=subj,
                body=body,
                from_email=campaign.sender,
                to=[user.email],
                bcc=bcc_pool or [],
                reply_to=[campaign.reply_to] if campaign.reply_to else [],
            )

            exc = _send_with_retry(msg, connection)
            if exc is None:
                log.status = NotificationLog.STATUS_DELIVERED
                log.save(update_fields=["status"])
                delivered += 1
            else:
                log.status = NotificationLog.STATUS_FAILED
                log.notes  = str(exc)
                log.save(update_fields=["status", "notes"])
                failed += 1
                logger.error(
                    "Send failed for campaign %s recipient %s: %s",
                    campaign_pk, user.email, exc,
                )

            # Batch boundary: flush counts to DB so the detail page
            # shows live progress, then pause to avoid SMTP rate limits.
            seq = i + 1
            if seq % BATCH_SIZE == 0:
                campaign.delivered_count = delivered
                campaign.failed_count    = failed
                campaign.save(update_fields=["delivered_count", "failed_count"])
                if seq < total:
                    logger.info(
                        "Campaign %s: batch %d done (%d/%d), sleeping %.1fs",
                        campaign_pk, seq // BATCH_SIZE, seq, total, BATCH_DELAY,
                    )
                    time.sleep(BATCH_DELAY)

    finally:
        try:
            connection.close()
        except Exception:
            pass

    campaign.delivered_count = delivered
    campaign.failed_count    = failed
    campaign.recipient_count = delivered + failed
    campaign.status = (
        NotificationCampaign.STATUS_SENT    if failed == 0
        else NotificationCampaign.STATUS_PARTIAL if delivered > 0
        else NotificationCampaign.STATUS_FAILED
    )
    campaign.completed_at = timezone.now()
    campaign.save(update_fields=[
        "delivered_count", "failed_count", "recipient_count",
        "status", "completed_at",
    ])
    logger.info(
        "Campaign %s complete: %d delivered, %d failed (of %d total)",
        campaign_pk, delivered, failed, total,
    )


def _fail_campaign(campaign, reason: str):
    from .models import NotificationCampaign
    campaign.status = NotificationCampaign.STATUS_FAILED
    campaign.completed_at = timezone.now()
    campaign.save(update_fields=["status", "completed_at"])
    logger.error("Campaign %s FAILED: %s", campaign.pk, reason)
