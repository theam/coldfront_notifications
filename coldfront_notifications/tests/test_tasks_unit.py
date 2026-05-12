"""Unit tests for coldfront_notifications.tasks (pure logic, no DB)."""
import smtplib
import unittest
from unittest.mock import MagicMock, patch

import coldfront_notifications.tests.django_setup  # noqa: F401

from coldfront_notifications.resolvers import MissingValue
from coldfront_notifications.tasks import (
    _build_values,
    _is_transient,
    _render,
    _send_with_retry,
)


class TestRender(unittest.TestCase):
    """Tests for _render()."""

    def test_no_tokens_passthrough(self):
        self.assertEqual(_render("Hello world", {}), "Hello world")

    def test_one_token_substituted(self):
        self.assertEqual(_render("Hello {{name}}", {"name": "Alice"}), "Hello Alice")

    def test_missing_key_left_as_literal(self):
        self.assertEqual(_render("Hello {{name}}", {}), "Hello {{name}}")

    def test_multiple_tokens_all_substituted(self):
        template = "Dear {{first}} {{last}}, welcome to {{org}}."
        values = {"first": "Jane", "last": "Doe", "org": "FASRC"}
        self.assertEqual(_render(template, values), "Dear Jane Doe, welcome to FASRC.")

    def test_repeated_token_substituted(self):
        self.assertEqual(
            _render("{{x}} and {{x}}", {"x": "hi"}),
            "hi and hi",
        )

    def test_none_template_returns_empty(self):
        self.assertEqual(_render(None, {"foo": "bar"}), "")

    def test_empty_template(self):
        self.assertEqual(_render("", {"foo": "bar"}), "")

    def test_partial_match(self):
        result = _render("{{a}} {{b}}", {"a": "X"})
        self.assertEqual(result, "X {{b}}")


class TestIsTransient(unittest.TestCase):
    """Tests for _is_transient()."""

    def test_smtp_421_is_transient(self):
        exc = smtplib.SMTPResponseException(421, "Service not available")
        self.assertTrue(_is_transient(exc))

    def test_smtp_450_is_transient(self):
        exc = smtplib.SMTPResponseException(450, "Mailbox busy")
        self.assertTrue(_is_transient(exc))

    def test_smtp_499_is_transient(self):
        exc = smtplib.SMTPResponseException(499, "Custom 4xx")
        self.assertTrue(_is_transient(exc))

    def test_smtp_550_is_not_transient(self):
        exc = smtplib.SMTPResponseException(550, "Mailbox not found")
        self.assertFalse(_is_transient(exc))

    def test_smtp_500_is_not_transient(self):
        exc = smtplib.SMTPResponseException(500, "Syntax error")
        self.assertFalse(_is_transient(exc))

    def test_smtp_354_is_not_transient(self):
        exc = smtplib.SMTPResponseException(354, "Start mail input")
        self.assertFalse(_is_transient(exc))

    def test_smtp_server_disconnected_is_transient(self):
        exc = smtplib.SMTPServerDisconnected("Connection lost")
        self.assertTrue(_is_transient(exc))

    def test_connection_error_is_transient(self):
        exc = ConnectionError("refused")
        self.assertTrue(_is_transient(exc))

    def test_connection_reset_is_transient(self):
        exc = ConnectionResetError("reset by peer")
        self.assertTrue(_is_transient(exc))

    def test_value_error_is_not_transient(self):
        exc = ValueError("bad value")
        self.assertFalse(_is_transient(exc))

    def test_runtime_error_is_not_transient(self):
        exc = RuntimeError("something broke")
        self.assertFalse(_is_transient(exc))


class TestBuildValues(unittest.TestCase):

    def _make_var(self, source="manual", resolver_key="", value="", is_required=True):
        v = MagicMock()
        v.source = source
        v.SOURCE_MANUAL = "manual"
        v.resolver_key = resolver_key
        v.value = value
        v.is_required = is_required
        v.key = "test_var"
        return v

    def test_manual_var_with_value(self):
        var = self._make_var(value="2026-06-01")
        result = _build_values(["date"], {"date": var}, {"user": MagicMock()})
        self.assertEqual(result["date"], "2026-06-01")

    def test_manual_var_required_empty_raises(self):
        var = self._make_var(value="", is_required=True)
        with self.assertRaises(MissingValue):
            _build_values(["date"], {"date": var}, {"user": MagicMock()})

    def test_manual_var_not_required_empty_returns_empty(self):
        var = self._make_var(value="", is_required=False)
        result = _build_values(["date"], {"date": var}, {"user": MagicMock()})
        self.assertEqual(result["date"], "")

    @patch("coldfront_notifications.resolvers.resolve", return_value="alice@test.com")
    def test_query_var_delegates_to_resolve(self, mock_resolve):
        var = self._make_var(source="query", resolver_key="user.email")
        ctx = {"user": MagicMock(email="alice@test.com")}
        result = _build_values(["email"], {"email": var}, ctx)
        self.assertEqual(result["email"], "alice@test.com")
        mock_resolve.assert_called_once_with("user.email", ctx)

    @patch("coldfront_notifications.resolvers.resolve", side_effect=MissingValue("no value"))
    def test_query_var_resolution_failure_includes_context(self, _):
        var = self._make_var(source="query", resolver_key="project.title")
        user = MagicMock()
        user.email = "bob@test.com"
        with self.assertRaises(MissingValue) as ctx:
            _build_values(["title"], {"title": var}, {"user": user})
        self.assertIn("project.title", str(ctx.exception))
        self.assertIn("bob@test.com", str(ctx.exception))

    def test_unknown_token_raises(self):
        with self.assertRaises(MissingValue) as ctx:
            _build_values(["nonexistent"], {}, {"user": MagicMock()})
        self.assertIn("nonexistent", str(ctx.exception))


class TestSendWithRetry(unittest.TestCase):

    def _make_msg_and_conn(self):
        msg = MagicMock()
        conn = MagicMock()
        return msg, conn

    def test_success_first_try(self):
        msg, conn = self._make_msg_and_conn()
        result = _send_with_retry(msg, conn, max_retries=2, base_delay=0)
        self.assertIsNone(result)
        msg.send.assert_called_once()

    def test_permanent_error_returns_immediately(self):
        msg, conn = self._make_msg_and_conn()
        exc = smtplib.SMTPResponseException(550, "Mailbox not found")
        msg.send.side_effect = exc
        result = _send_with_retry(msg, conn, max_retries=2, base_delay=0)
        self.assertIs(result, exc)
        self.assertEqual(msg.send.call_count, 1)

    @patch("coldfront_notifications.tasks.time.sleep")
    def test_transient_then_success(self, mock_sleep):
        msg, conn = self._make_msg_and_conn()
        exc = smtplib.SMTPResponseException(421, "Try again")
        msg.send.side_effect = [exc, None]
        result = _send_with_retry(msg, conn, max_retries=2, base_delay=0.01)
        self.assertIsNone(result)
        self.assertEqual(msg.send.call_count, 2)

    @patch("coldfront_notifications.tasks.time.sleep")
    def test_transient_exhausts_retries(self, mock_sleep):
        msg, conn = self._make_msg_and_conn()
        exc = smtplib.SMTPResponseException(450, "Busy")
        msg.send.side_effect = exc
        result = _send_with_retry(msg, conn, max_retries=2, base_delay=0.01)
        self.assertIs(result, exc)
        self.assertEqual(msg.send.call_count, 3)

    @patch("coldfront_notifications.tasks.time.sleep")
    def test_disconnect_reopens_connection(self, mock_sleep):
        msg, conn = self._make_msg_and_conn()
        exc = smtplib.SMTPServerDisconnected("gone")
        msg.send.side_effect = [exc, None]
        result = _send_with_retry(msg, conn, max_retries=2, base_delay=0.01)
        self.assertIsNone(result)
        conn.close.assert_called()
        conn.open.assert_called()

    @patch("coldfront_notifications.tasks.time.sleep")
    def test_reopen_failure_returns_exception(self, mock_sleep):
        msg, conn = self._make_msg_and_conn()
        msg.send.side_effect = smtplib.SMTPServerDisconnected("gone")
        reopen_exc = ConnectionError("cannot reopen")
        conn.open.side_effect = reopen_exc
        result = _send_with_retry(msg, conn, max_retries=2, base_delay=0.01)
        self.assertIs(result, reopen_exc)

    @patch("coldfront_notifications.tasks.time.sleep")
    def test_close_failure_is_swallowed(self, mock_sleep):
        """connection.close() throwing should not prevent the retry."""
        msg, conn = self._make_msg_and_conn()
        exc = smtplib.SMTPResponseException(421, "Try again")
        msg.send.side_effect = [exc, None]
        conn.close.side_effect = OSError("close failed")
        result = _send_with_retry(msg, conn, max_retries=2, base_delay=0.01)
        self.assertIsNone(result)
        self.assertEqual(msg.send.call_count, 2)


if __name__ == "__main__":
    unittest.main()
