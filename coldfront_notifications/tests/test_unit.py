"""
Unit tests for coldfront_notifications plugin.

These tests exercise pure logic functions and do NOT require a database.
Run with: python -m pytest coldfront_notifications/tests/test_unit.py
"""
import re
import smtplib
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# We need Django settings configured before importing plugin modules.
# ---------------------------------------------------------------------------
import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
        ],
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    django.setup()


from coldfront_notifications.resolvers import (
    QUERY_CHOICES,
    QUERY_RESOLVERS,
    QUERY_SCOPES,
    MissingValue,
    _fmt,
    _need,
    _scope_for,
    resolve,
)
from coldfront_notifications.tasks import TOKEN_RE as TASKS_TOKEN_RE, _is_transient, _render
from coldfront_notifications.validators import (
    TOKEN_RE as VALIDATORS_TOKEN_RE,
    _extract_tokens,
    _required_scope,
)


# ===========================================================================
# resolvers.py tests
# ===========================================================================


class TestFmt(unittest.TestCase):
    """Tests for _fmt()."""

    def test_valid_string(self):
        self.assertEqual(_fmt("hello"), "hello")

    def test_strips_whitespace(self):
        self.assertEqual(_fmt("  world  "), "world")

    def test_none_raises_missing(self):
        with self.assertRaises(MissingValue):
            _fmt(None)

    def test_empty_string_raises_missing(self):
        with self.assertRaises(MissingValue):
            _fmt("")

    def test_whitespace_only_raises_missing(self):
        with self.assertRaises(MissingValue):
            _fmt("   ")

    def test_coerces_to_string(self):
        self.assertEqual(_fmt(42), "42")


class TestNeed(unittest.TestCase):
    """Tests for _need()."""

    def test_present_key_returns_value(self):
        ctx = {"user": "alice"}
        self.assertEqual(_need(ctx, "user"), "alice")

    def test_missing_key_raises_missing(self):
        ctx = {"user": "alice"}
        with self.assertRaises(MissingValue):
            _need(ctx, "project")

    def test_none_value_raises_missing(self):
        ctx = {"project": None}
        with self.assertRaises(MissingValue):
            _need(ctx, "project")


class TestResolve(unittest.TestCase):
    """Tests for resolve()."""

    def test_valid_resolver_key_dispatches(self):
        user = MagicMock()
        user.username = "jdoe"
        ctx = {"user": user, "project": None, "allocation": None}
        result = resolve("user.username", ctx)
        self.assertEqual(result, "jdoe")

    def test_unknown_key_raises_missing(self):
        ctx = {"user": MagicMock(), "project": None, "allocation": None}
        with self.assertRaises(MissingValue):
            resolve("nonexistent.key", ctx)

    def test_attribute_error_raises_missing(self):
        # user with no .username attribute at all
        user = MagicMock(spec=[])
        ctx = {"user": user, "project": None, "allocation": None}
        with self.assertRaises(MissingValue):
            resolve("user.username", ctx)

    def test_resolve_user_email(self):
        user = MagicMock()
        user.email = "alice@example.com"
        ctx = {"user": user, "project": None, "allocation": None}
        self.assertEqual(resolve("user.email", ctx), "alice@example.com")

    def test_resolve_user_full_name(self):
        user = MagicMock()
        user.get_full_name.return_value = "Jane Doe"
        ctx = {"user": user, "project": None, "allocation": None}
        self.assertEqual(resolve("user.full_name", ctx), "Jane Doe")

    def test_resolve_user_full_name_falls_back_to_username(self):
        user = MagicMock()
        user.get_full_name.return_value = ""
        user.username = "jdoe"
        ctx = {"user": user, "project": None, "allocation": None}
        self.assertEqual(resolve("user.full_name", ctx), "jdoe")


class TestQueryChoicesConsistency(unittest.TestCase):
    """Tests that QUERY_CHOICES, QUERY_RESOLVERS, and QUERY_SCOPES are consistent."""

    def test_choices_keys_match_resolvers(self):
        choice_keys = {k for k, _label, _group in QUERY_CHOICES}
        resolver_keys = set(QUERY_RESOLVERS.keys())
        self.assertEqual(choice_keys, resolver_keys)

    def test_scopes_maps_every_choice(self):
        choice_keys = {k for k, _label, _group in QUERY_CHOICES}
        scope_keys = set(QUERY_SCOPES.keys())
        self.assertEqual(choice_keys, scope_keys)


class TestScopeFor(unittest.TestCase):
    """Tests for _scope_for() classification."""

    def test_user_scope(self):
        self.assertEqual(_scope_for("user.username"), "user")
        self.assertEqual(_scope_for("user.email"), "user")
        self.assertEqual(_scope_for("user.ifxid"), "user")

    def test_project_scope(self):
        self.assertEqual(_scope_for("project.title"), "project")
        self.assertEqual(_scope_for("project.pi.email"), "project")
        self.assertEqual(_scope_for("projectuser.role.name"), "project")

    def test_allocation_scope(self):
        self.assertEqual(_scope_for("allocation.id"), "allocation")
        self.assertEqual(_scope_for("allocation.status.name"), "allocation")

    def test_resource_scope(self):
        self.assertEqual(_scope_for("resource.name"), "allocation")
        self.assertEqual(_scope_for("resource.description"), "allocation")


# ===========================================================================
# validators.py tests
# ===========================================================================


class TestExtractTokens(unittest.TestCase):
    """Tests for _extract_tokens()."""

    def test_no_tokens(self):
        self.assertEqual(_extract_tokens("Hello world", "No tokens here"), [])

    def test_one_token_in_subject(self):
        self.assertEqual(_extract_tokens("Hello {{name}}", "body"), ["name"])

    def test_one_token_in_body(self):
        self.assertEqual(_extract_tokens("subj", "Hello {{name}}"), ["name"])

    def test_duplicates_deduped_order_preserved(self):
        result = _extract_tokens("{{a}} {{b}} {{a}}", "{{b}} {{c}}")
        self.assertEqual(result, ["a", "b", "c"])

    def test_nested_braces_only_valid_matches(self):
        # {{{foo}}} should still find 'foo' via \w+ inside {{ }}
        result = _extract_tokens("{{{foo}}}", "")
        self.assertEqual(result, ["foo"])

    def test_invalid_tokens_ignored(self):
        # Tokens with spaces or special chars shouldn't match \w+
        result = _extract_tokens("{{hello world}} {{good}}", "")
        self.assertEqual(result, ["good"])

    def test_empty_strings(self):
        self.assertEqual(_extract_tokens("", ""), [])

    def test_none_subject(self):
        self.assertEqual(_extract_tokens(None, "{{x}}"), ["x"])

    def test_multiple_tokens(self):
        result = _extract_tokens("{{first}} {{last}}", "Dear {{first}}")
        self.assertEqual(result, ["first", "last"])


class TestRequiredScope(unittest.TestCase):
    """Tests for _required_scope()."""

    def _make_var(self, resolver_key, source="query"):
        v = MagicMock()
        v.source = "query"
        v.SOURCE_QUERY = "query"
        v.resolver_key = resolver_key
        return v

    def _make_manual_var(self):
        v = MagicMock()
        v.source = "manual"
        v.SOURCE_QUERY = "query"
        v.resolver_key = None
        return v

    def test_no_vars_returns_user(self):
        self.assertEqual(_required_scope([]), "user")

    def test_only_user_vars_returns_user(self):
        vars_list = [self._make_var("user.email"), self._make_var("user.username")]
        self.assertEqual(_required_scope(vars_list), "user")

    def test_project_vars_returns_project(self):
        vars_list = [self._make_var("project.title")]
        self.assertEqual(_required_scope(vars_list), "project")

    def test_allocation_vars_returns_allocation(self):
        vars_list = [self._make_var("allocation.id")]
        self.assertEqual(_required_scope(vars_list), "allocation")

    def test_resource_vars_returns_allocation(self):
        vars_list = [self._make_var("resource.name")]
        self.assertEqual(_required_scope(vars_list), "allocation")

    def test_mix_highest_wins_allocation(self):
        vars_list = [
            self._make_var("user.email"),
            self._make_var("project.title"),
            self._make_var("allocation.id"),
        ]
        self.assertEqual(_required_scope(vars_list), "allocation")

    def test_mix_project_beats_user(self):
        vars_list = [
            self._make_var("user.email"),
            self._make_var("project.title"),
        ]
        self.assertEqual(_required_scope(vars_list), "project")

    def test_manual_vars_dont_affect_scope(self):
        vars_list = [self._make_manual_var()]
        self.assertEqual(_required_scope(vars_list), "user")

    def test_manual_with_allocation_still_allocation(self):
        vars_list = [self._make_manual_var(), self._make_var("allocation.id")]
        self.assertEqual(_required_scope(vars_list), "allocation")


# ===========================================================================
# tasks.py tests
# ===========================================================================


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
        # Only some tokens have values
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


# ===========================================================================
# conf.py tests
# ===========================================================================


class TestConfDefaults(unittest.TestCase):
    """Tests that conf.py exports sensible defaults."""

    def test_batch_size_default(self):
        from coldfront_notifications.conf import BATCH_SIZE
        self.assertEqual(BATCH_SIZE, 50)

    def test_batch_delay_default(self):
        from coldfront_notifications.conf import BATCH_DELAY
        self.assertEqual(BATCH_DELAY, 2.0)

    def test_max_retries_default(self):
        from coldfront_notifications.conf import MAX_RETRIES
        self.assertEqual(MAX_RETRIES, 3)

    def test_retry_delay_default(self):
        from coldfront_notifications.conf import RETRY_DELAY
        self.assertEqual(RETRY_DELAY, 1.0)

    def test_preview_page_size_default(self):
        from coldfront_notifications.conf import PREVIEW_PAGE_SIZE
        self.assertEqual(PREVIEW_PAGE_SIZE, 50)

    def test_preview_render_limit_default(self):
        from coldfront_notifications.conf import PREVIEW_RENDER_LIMIT
        self.assertEqual(PREVIEW_RENDER_LIMIT, 10)


# ===========================================================================
# TOKEN_RE pattern tests (shared pattern used in tasks and validators)
# ===========================================================================


class TestTokenRegex(unittest.TestCase):
    """Tests for the TOKEN_RE pattern used across modules."""

    def test_matches_simple_token(self):
        m = TASKS_TOKEN_RE.search("{{foo}}")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "foo")

    def test_matches_underscore_token(self):
        m = TASKS_TOKEN_RE.search("{{first_name}}")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "first_name")

    def test_no_match_spaces(self):
        m = TASKS_TOKEN_RE.search("{{ foo }}")
        self.assertIsNone(m)

    def test_no_match_special_chars(self):
        m = TASKS_TOKEN_RE.search("{{foo-bar}}")
        self.assertIsNone(m)

    def test_validators_and_tasks_same_pattern(self):
        self.assertEqual(TASKS_TOKEN_RE.pattern, VALIDATORS_TOKEN_RE.pattern)


if __name__ == "__main__":
    unittest.main()
