"""Unit tests for coldfront_notifications.campaign_sender (pure logic, no DB)."""
import smtplib
import unittest
from unittest.mock import MagicMock, patch

import coldfront_notifications.tests.django_setup  # noqa: F401

from coldfront_notifications.template_variable_value_resolver import MissingValue
from coldfront_notifications.campaign_sender import (
    TemplateRenderer,
    SmtpDelivery,
)


class TestTemplateRendererRender(unittest.TestCase):
    """Tests for TemplateRenderer.render()."""

    def setUp(self):
        self.renderer = TemplateRenderer()

    def test_no_tokens_passthrough(self):
        self.assertEqual(self.renderer.render("Hello world", {}), "Hello world")

    def test_one_token_substituted(self):
        self.assertEqual(self.renderer.render("Hello {{name}}", {"name": "Alice"}), "Hello Alice")

    def test_missing_key_left_as_literal(self):
        self.assertEqual(self.renderer.render("Hello {{name}}", {}), "Hello {{name}}")

    def test_multiple_tokens_all_substituted(self):
        template = "Dear {{first}} {{last}}, welcome to {{org}}."
        values = {"first": "Jane", "last": "Doe", "org": "FASRC"}
        self.assertEqual(self.renderer.render(template, values), "Dear Jane Doe, welcome to FASRC.")

    def test_repeated_token_substituted(self):
        self.assertEqual(self.renderer.render("{{x}} and {{x}}", {"x": "hi"}), "hi and hi")

    def test_none_template_returns_empty(self):
        self.assertEqual(self.renderer.render(None, {"foo": "bar"}), "")

    def test_empty_template(self):
        self.assertEqual(self.renderer.render("", {"foo": "bar"}), "")

    def test_partial_match(self):
        result = self.renderer.render("{{a}} {{b}}", {"a": "X"})
        self.assertEqual(result, "X {{b}}")


class TestSmtpDeliveryIsTransient(unittest.TestCase):
    """Tests for SmtpDelivery.is_transient_error()."""

    def setUp(self):
        self.delivery = SmtpDelivery()

    def test_smtp_421_is_transient(self):
        exception = smtplib.SMTPResponseException(421, "Service not available")
        self.assertTrue(self.delivery.is_transient_error(exception))

    def test_smtp_450_is_transient(self):
        exception = smtplib.SMTPResponseException(450, "Mailbox busy")
        self.assertTrue(self.delivery.is_transient_error(exception))

    def test_smtp_499_is_transient(self):
        exception = smtplib.SMTPResponseException(499, "Custom 4xx")
        self.assertTrue(self.delivery.is_transient_error(exception))

    def test_smtp_550_is_not_transient(self):
        exception = smtplib.SMTPResponseException(550, "Mailbox not found")
        self.assertFalse(self.delivery.is_transient_error(exception))

    def test_smtp_500_is_not_transient(self):
        exception = smtplib.SMTPResponseException(500, "Syntax error")
        self.assertFalse(self.delivery.is_transient_error(exception))

    def test_smtp_354_is_not_transient(self):
        exception = smtplib.SMTPResponseException(354, "Start mail input")
        self.assertFalse(self.delivery.is_transient_error(exception))

    def test_smtp_server_disconnected_is_transient(self):
        exception = smtplib.SMTPServerDisconnected("Connection lost")
        self.assertTrue(self.delivery.is_transient_error(exception))

    def test_connection_error_is_transient(self):
        exception = ConnectionError("refused")
        self.assertTrue(self.delivery.is_transient_error(exception))

    def test_connection_reset_is_transient(self):
        exception = ConnectionResetError("reset by peer")
        self.assertTrue(self.delivery.is_transient_error(exception))

    def test_value_error_is_not_transient(self):
        exception = ValueError("bad value")
        self.assertFalse(self.delivery.is_transient_error(exception))

    def test_runtime_error_is_not_transient(self):
        exception = RuntimeError("something broke")
        self.assertFalse(self.delivery.is_transient_error(exception))


class TestTemplateRendererBuildValues(unittest.TestCase):
    """Tests for TemplateRenderer.build_values()."""

    def setUp(self):
        self.renderer = TemplateRenderer()

    def _make_variable(self, source="manual", resolver_key="", value="", is_required=True):
        variable = MagicMock()
        variable.source = source
        variable.Source.MANUAL = "manual"
        variable.resolver_key = resolver_key
        variable.value = value
        variable.is_required = is_required
        variable.key = "test_var"
        return variable

    def test_manual_variable_with_value(self):
        variable = self._make_variable(value="2026-06-01")
        result = self.renderer.build_values(["date"], {"date": variable}, {"user": MagicMock()})
        self.assertEqual(result["date"], "2026-06-01")

    def test_manual_variable_required_empty_raises(self):
        variable = self._make_variable(value="", is_required=True)
        with self.assertRaises(MissingValue):
            self.renderer.build_values(["date"], {"date": variable}, {"user": MagicMock()})

    def test_manual_variable_not_required_empty_returns_empty(self):
        variable = self._make_variable(value="", is_required=False)
        result = self.renderer.build_values(["date"], {"date": variable}, {"user": MagicMock()})
        self.assertEqual(result["date"], "")

    @patch("coldfront_notifications.campaign_sender.resolver_registry.resolve", return_value="alice@test.com")
    def test_query_variable_delegates_to_resolver(self, mock_resolve):
        variable = self._make_variable(source="query", resolver_key="user.email")
        context = {"user": MagicMock(email="alice@test.com")}
        result = self.renderer.build_values(["email"], {"email": variable}, context)
        self.assertEqual(result["email"], "alice@test.com")

    @patch("coldfront_notifications.campaign_sender.resolver_registry.resolve", side_effect=MissingValue("no value"))
    def test_query_variable_resolution_failure_includes_context(self, _mock):
        variable = self._make_variable(source="query", resolver_key="project.title")
        user = MagicMock()
        user.email = "bob@test.com"
        with self.assertRaises(MissingValue) as error_context:
            self.renderer.build_values(["title"], {"title": variable}, {"user": user})
        self.assertIn("project.title", str(error_context.exception))
        self.assertIn("bob@test.com", str(error_context.exception))

    def test_unknown_token_raises(self):
        with self.assertRaises(MissingValue) as error_context:
            self.renderer.build_values(["nonexistent"], {}, {"user": MagicMock()})
        self.assertIn("nonexistent", str(error_context.exception))


class TestSmtpDeliverySendWithRetry(unittest.TestCase):
    """Tests for SmtpDelivery.send_with_retry()."""

    def setUp(self):
        self.delivery = SmtpDelivery(max_retries=2, retry_delay=0.01)

    def _make_message_and_connection(self):
        message = MagicMock()
        connection = MagicMock()
        return message, connection

    def test_success_first_try(self):
        message, connection = self._make_message_and_connection()
        result = self.delivery.send_with_retry(message, connection)
        self.assertIsNone(result)
        message.send.assert_called_once()

    def test_permanent_error_returns_immediately(self):
        message, connection = self._make_message_and_connection()
        exception = smtplib.SMTPResponseException(550, "Mailbox not found")
        message.send.side_effect = exception
        result = self.delivery.send_with_retry(message, connection)
        self.assertIs(result, exception)
        self.assertEqual(message.send.call_count, 1)

    @patch("coldfront_notifications.campaign_sender.time.sleep")
    def test_transient_then_success(self, _mock_sleep):
        message, connection = self._make_message_and_connection()
        exception = smtplib.SMTPResponseException(421, "Try again")
        message.send.side_effect = [exception, None]
        result = self.delivery.send_with_retry(message, connection)
        self.assertIsNone(result)
        self.assertEqual(message.send.call_count, 2)

    @patch("coldfront_notifications.campaign_sender.time.sleep")
    def test_transient_exhausts_retries(self, _mock_sleep):
        message, connection = self._make_message_and_connection()
        exception = smtplib.SMTPResponseException(450, "Busy")
        message.send.side_effect = exception
        result = self.delivery.send_with_retry(message, connection)
        self.assertIs(result, exception)
        self.assertEqual(message.send.call_count, 3)

    @patch("coldfront_notifications.campaign_sender.time.sleep")
    def test_disconnect_reopens_connection(self, _mock_sleep):
        message, connection = self._make_message_and_connection()
        exception = smtplib.SMTPServerDisconnected("gone")
        message.send.side_effect = [exception, None]
        result = self.delivery.send_with_retry(message, connection)
        self.assertIsNone(result)
        connection.close.assert_called()
        connection.open.assert_called()

    @patch("coldfront_notifications.campaign_sender.time.sleep")
    def test_reopen_failure_raises(self, _mock_sleep):
        message, connection = self._make_message_and_connection()
        message.send.side_effect = smtplib.SMTPServerDisconnected("gone")
        reopen_exception = ConnectionError("cannot reopen")
        connection.open.side_effect = reopen_exception
        with self.assertRaises(ConnectionError):
            self.delivery.send_with_retry(message, connection)

    @patch("coldfront_notifications.campaign_sender.time.sleep")
    def test_close_failure_is_swallowed(self, _mock_sleep):
        """connection.close() throwing should not prevent the retry."""
        message, connection = self._make_message_and_connection()
        exception = smtplib.SMTPResponseException(421, "Try again")
        message.send.side_effect = [exception, None]
        connection.close.side_effect = OSError("close failed")
        result = self.delivery.send_with_retry(message, connection)
        self.assertIsNone(result)
        self.assertEqual(message.send.call_count, 2)


class TestSmtpDeliveryReopenConnection(unittest.TestCase):
    """Tests for SmtpDelivery._reopen_connection()."""

    def setUp(self):
        self.delivery = SmtpDelivery()

    def test_reopen_closes_and_opens(self):
        connection = MagicMock()
        self.delivery._reopen_connection(connection)
        connection.close.assert_called_once()
        connection.open.assert_called_once()

    def test_close_failure_swallowed(self):
        connection = MagicMock()
        connection.close.side_effect = OSError("close failed")
        self.delivery._reopen_connection(connection)
        connection.open.assert_called_once()

    def test_open_failure_raises(self):
        connection = MagicMock()
        connection.open.side_effect = ConnectionError("cannot open")
        with self.assertRaises(ConnectionError):
            self.delivery._reopen_connection(connection)


class TestCampaignSender(unittest.TestCase):
    """Tests for CampaignSender orchestration."""

    @patch("coldfront_notifications.campaign_sender.NotificationVariable")
    @patch("coldfront_notifications.campaign_sender.extract_tokens", return_value=["name"])
    @patch("coldfront_notifications.campaign_sender.determine_scope", return_value="project")
    @patch("coldfront_notifications.campaign_sender.RecipientResolver")
    @patch("coldfront_notifications.campaign_sender.get_connection")
    @patch("coldfront_notifications.campaign_sender.timezone")
    @patch("coldfront_notifications.campaign_sender.NotificationLog")
    def test_send_with_no_recipients_fails(self, mock_log, mock_tz, mock_conn,
                                            mock_resolver_cls, mock_scope,
                                            mock_tokens, mock_nv):
        from coldfront_notifications.campaign_sender import CampaignSender

        campaign = MagicMock()
        campaign.filters_snapshot = {}
        campaign.subject = "Hi {{name}}"
        campaign.body = "Body"
        campaign.logs.all.return_value.delete.return_value = None

        mock_var = MagicMock()
        mock_var.key = "name"
        mock_nv.objects.filter.return_value = [mock_var]

        mock_resolver = MagicMock()
        mock_resolver.enumerate_deduped.return_value = iter([])
        mock_resolver_cls.return_value = mock_resolver

        sender = CampaignSender(campaign)
        sender.send()

        campaign.save.assert_called()
        self.assertEqual(campaign.status, "failed")

    def test_fail_sets_status_and_saves(self):
        from coldfront_notifications.campaign_sender import CampaignSender

        campaign = MagicMock()
        sender = CampaignSender(campaign)
        sender._fail("test reason")

        self.assertEqual(campaign.status, "failed")
        campaign.save.assert_called_once()


class TestSendNotificationCampaignTask(unittest.TestCase):
    """Tests for the celery task wrapper."""

    @patch("coldfront_notifications.campaign_sender.NotificationCampaign")
    def test_missing_campaign_logs_error(self, mock_model):
        from coldfront_notifications.campaign_sender import send_notification_campaign
        from coldfront_notifications.models import NotificationCampaign

        mock_model.DoesNotExist = NotificationCampaign.DoesNotExist
        mock_model.objects.get.side_effect = NotificationCampaign.DoesNotExist
        send_notification_campaign(999)
        mock_model.objects.get.assert_called_once_with(pk=999)

    @patch("coldfront_notifications.campaign_sender.CampaignSender")
    @patch("coldfront_notifications.campaign_sender.NotificationCampaign")
    def test_delegates_to_campaign_sender(self, mock_model, mock_sender_cls):
        from coldfront_notifications.campaign_sender import send_notification_campaign

        campaign = MagicMock()
        mock_model.objects.get.return_value = campaign

        send_notification_campaign(1)

        mock_sender_cls.assert_called_once_with(campaign)
        mock_sender_cls.return_value.send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
