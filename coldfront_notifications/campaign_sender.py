"""
Celery task and sender logic for notification campaigns.

CampaignSender resolves template variables for each recipient, renders
the subject/body, and sends emails in batches over a single SMTP connection
with retry on transient errors.
"""
from __future__ import annotations

import logging
import re
import smtplib
import time

from django.core.mail import EmailMessage, get_connection
from django.utils import timezone

from .conf import BATCH_SIZE, BATCH_DELAY, MAX_RETRIES, RETRY_DELAY
from .filters import RecipientResolver
from .models import NotificationCampaign, NotificationLog, NotificationVariable
from .notification_validator import extract_tokens, determine_scope
from .template_variable_value_resolver import MissingValue, registry as resolver_registry

logger = logging.getLogger(__name__)

try:
    from celery import shared_task
except ImportError:
    def shared_task(fn):
        return fn

TOKEN_PATTERN = re.compile(r"\{\{(\w+)\}\}")


class TemplateRenderer:
    """Renders {{token}} placeholders in subject/body strings."""

    def render(self, template_string: str, values: dict) -> str:
        def replace_token(match):
            token = match.group(1)
            return values[token] if token in values else match.group(0)
        return TOKEN_PATTERN.sub(replace_token, template_string or "")

    def build_values(self, tokens: list[str], variables_by_key: dict, context: dict) -> dict:
        """Resolve all token values for a single recipient context."""
        values = {}
        for token in tokens:
            variable = variables_by_key.get(token)
            if variable is None:
                raise MissingValue(f"unknown token {token!r}")

            if variable.source == NotificationVariable.Source.MANUAL:
                stored_value = (variable.value or "").strip()
                if not stored_value and variable.is_required:
                    raise MissingValue(f"manual variable {token!r} has no stored value")
                values[token] = variable.value or ""
            else:
                try:
                    values[token] = resolver_registry.resolve(variable.resolver_key, context)
                except MissingValue:
                    raise MissingValue(
                        f"cannot resolve {variable.resolver_key} for {context['user'].email}"
                    )
        return values


class SmtpDelivery:
    """Handles SMTP delivery with retry logic for transient errors."""

    def __init__(self, max_retries: int = MAX_RETRIES, retry_delay: float = RETRY_DELAY):
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    def is_transient_error(self, exception) -> bool:
        if isinstance(exception, smtplib.SMTPResponseException):
            return 400 <= exception.smtp_code < 500
        if isinstance(exception, smtplib.SMTPServerDisconnected):
            return True
        if isinstance(exception, ConnectionError):
            return True
        return False

    def send_with_retry(self, message: EmailMessage, connection) -> Exception | None:
        """Send a message with retry on transient errors.

        Returns None on success or the exception on permanent failure.
        """
        last_exception = None
        for attempt in range(self.max_retries + 1):
            try:
                message.connection = connection
                message.send()
                return None
            except Exception as exception:
                last_exception = exception
                if not self.is_transient_error(exception) or attempt == self.max_retries:
                    return exception

                delay = self.retry_delay * (2 ** attempt)
                logger.warning(
                    "Transient SMTP error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, self.max_retries + 1, delay, exception,
                )
                time.sleep(delay)
                self._reopen_connection(connection)
        return last_exception

    def _reopen_connection(self, connection):
        try:
            connection.close()
        except Exception:
            pass
        try:
            connection.open()
        except Exception as reopen_exception:
            logger.error("Failed to re-open SMTP connection: %s", reopen_exception)
            raise


class CampaignSender:
    """Orchestrates sending a notification campaign.

    Phase 1: Resolve all template variables for every recipient.
    Phase 2: Send emails in batches over a single SMTP connection.
    """

    def __init__(self, campaign: NotificationCampaign):
        self.campaign = campaign
        self.renderer = TemplateRenderer()
        self.delivery = SmtpDelivery()

    def send(self):
        """Execute the full send pipeline."""
        campaign = self.campaign

        campaign.status = NotificationCampaign.Status.SENDING
        campaign.sent_at = timezone.now()
        campaign.save(update_fields=["status", "sent_at"])
        campaign.logs.all().delete()

        tokens = extract_tokens(campaign.subject, campaign.body)
        variables_by_key = {
            variable.key: variable
            for variable in NotificationVariable.objects.filter(key__in=tokens)
        }

        unknown_tokens = [token for token in tokens if token not in variables_by_key]
        if unknown_tokens:
            self._fail(f"Unknown tokens: {', '.join(unknown_tokens)}")
            return

        scope = determine_scope(list(variables_by_key.values()))
        snapshot = campaign.filters_snapshot or {}
        dedupe_users = snapshot.get("dedupe_users") or []

        resolved_emails = self._resolve_all_recipients(
            tokens, variables_by_key, snapshot, scope, dedupe_users,
        )
        if resolved_emails is None:
            return  # _fail already called

        if not resolved_emails:
            self._fail("No recipients matched the filters.")
            return

        campaign.recipient_count = len(resolved_emails)
        campaign.save(update_fields=["recipient_count"])

        logger.info(
            "Campaign %s: %d emails to send (batch_size=%d, delay=%.1fs)",
            campaign.pk, len(resolved_emails), BATCH_SIZE, BATCH_DELAY,
        )

        self._deliver_emails(resolved_emails, snapshot)

    def _resolve_all_recipients(self, tokens, variables_by_key, snapshot, scope, dedupe_users):
        """Resolve template variables for every recipient.

        Returns a list of (user, project, allocation, rendered_subject, rendered_body)
        tuples, or None if a resolution error occurred.
        """
        resolver = RecipientResolver(snapshot)
        resolved = []

        for user, project, allocation in resolver.enumerate_deduped(scope, dedupe_users):
            context = {"user": user, "project": project, "allocation": allocation}
            try:
                values = self.renderer.build_values(tokens, variables_by_key, context)
            except MissingValue as error:
                self._fail(f"Resolution error: {error}")
                return None

            resolved.append((
                user, project, allocation,
                self.renderer.render(self.campaign.subject, values),
                self.renderer.render(self.campaign.body, values),
            ))

        return resolved

    def _deliver_emails(self, resolved_emails, snapshot):
        """Send resolved emails in batches over a single SMTP connection."""
        campaign = self.campaign
        bcc_recipients = list(set(
            address.strip()
            for address in snapshot.get("extra_recipients", [])
            if address.strip()
        ))

        delivered_count = 0
        failed_count = 0

        connection = get_connection()
        try:
            connection.open()
        except Exception as exception:
            self._fail(f"Cannot open SMTP connection: {exception}")
            return

        try:
            for index, (user, project, allocation, rendered_subject, rendered_body) in enumerate(resolved_emails):
                log_entry = NotificationLog.objects.create(
                    campaign=campaign,
                    email=user.email,
                    status=NotificationLog.Status.QUEUED,
                    rendered_subject=rendered_subject,
                    rendered_body=rendered_body,
                    project_id=project.pk if project else None,
                    allocation_id=allocation.pk if allocation else None,
                )

                message = EmailMessage(
                    subject=rendered_subject,
                    body=rendered_body,
                    from_email=campaign.sender,
                    to=[user.email],
                    bcc=bcc_recipients if index == 0 else [],
                    reply_to=[campaign.reply_to] if campaign.reply_to else [],
                )

                send_error = self.delivery.send_with_retry(message, connection)
                if send_error is None:
                    log_entry.status = NotificationLog.Status.DELIVERED
                    delivered_count += 1
                else:
                    log_entry.status = NotificationLog.Status.FAILED
                    log_entry.notes = str(send_error)[:500]
                    failed_count += 1

                log_entry.save(update_fields=["status", "notes"])

                if (index + 1) % BATCH_SIZE == 0:
                    campaign.delivered_count = delivered_count
                    campaign.failed_count = failed_count
                    campaign.save(update_fields=["delivered_count", "failed_count"])

                    if index + 1 < len(resolved_emails):
                        logger.info(
                            "Campaign %s: batch pause after %d/%d emails",
                            campaign.pk, index + 1, len(resolved_emails),
                        )
                        time.sleep(BATCH_DELAY)
        finally:
            try:
                connection.close()
            except Exception:
                pass

        campaign.status = (
            NotificationCampaign.Status.SENT if failed_count == 0
            else NotificationCampaign.Status.PARTIAL if delivered_count > 0
            else NotificationCampaign.Status.FAILED
        )
        campaign.delivered_count = delivered_count
        campaign.failed_count = failed_count
        campaign.recipient_count = delivered_count + failed_count
        campaign.completed_at = timezone.now()
        campaign.save(update_fields=[
            "status", "delivered_count", "failed_count",
            "recipient_count", "completed_at",
        ])

        logger.info(
            "Campaign %s complete: %d delivered, %d failed (of %d total)",
            campaign.pk, delivered_count, failed_count, delivered_count + failed_count,
        )

    def _fail(self, reason: str):
        self.campaign.status = NotificationCampaign.Status.FAILED
        self.campaign.completed_at = timezone.now()
        self.campaign.save(update_fields=["status", "completed_at"])
        logger.error("Campaign %s FAILED: %s", self.campaign.pk, reason)


@shared_task
def send_notification_campaign(campaign_pk: int):
    try:
        campaign = NotificationCampaign.objects.get(pk=campaign_pk)
    except NotificationCampaign.DoesNotExist:
        logger.error("Campaign %s not found", campaign_pk)
        return
    CampaignSender(campaign).send()
