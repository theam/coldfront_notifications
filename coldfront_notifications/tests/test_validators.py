"""Unit tests for coldfront_notifications.validators."""
import unittest
from unittest.mock import MagicMock, patch

import coldfront_notifications.tests.django_setup  # noqa: F401

from coldfront_notifications.resolvers import MissingValue
from coldfront_notifications.tasks import TOKEN_RE as TASKS_TOKEN_RE
from coldfront_notifications.validators import (
    TOKEN_RE as VALIDATORS_TOKEN_RE,
    _extract_tokens,
    _required_scope,
    validate_campaign,
)


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
        result = _extract_tokens("{{{foo}}}", "")
        self.assertEqual(result, ["foo"])

    def test_invalid_tokens_ignored(self):
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


class TestValidateCampaign(unittest.TestCase):

    PATCH_ENUM = "coldfront_notifications.validators.enumerate_recipients_deduped"
    PATCH_RESOLVE = "coldfront_notifications.validators.resolve"

    def _patch_nv_objects(self, return_value):
        patcher = patch(
            "coldfront_notifications.models.NotificationVariable.objects"
        )
        mock_objects = patcher.start()
        mock_objects.filter.return_value = return_value
        self.addCleanup(patcher.stop)

    def _make_var(self, key, source="query", resolver_key="user.email",
                  value="", is_required=True):
        v = MagicMock()
        v.key = key
        v.source = source
        v.SOURCE_QUERY = "query"
        v.SOURCE_MANUAL = "manual"
        v.resolver_key = resolver_key
        v.value = value
        v.is_required = is_required
        return v

    def _make_user(self, pk, username, email):
        u = MagicMock()
        u.pk = pk
        u.username = username
        u.email = email
        return u

    @patch(PATCH_ENUM)
    def test_all_tokens_resolve_cleanly(self, mock_enum):
        var = self._make_var("name", resolver_key="user.full_name")
        self._patch_nv_objects([var])

        user = self._make_user(1, "alice", "alice@test.com")
        mock_enum.return_value = [(user, None, None)]

        with patch(self.PATCH_RESOLVE, return_value="Alice"):
            result = validate_campaign("Hi {{name}}", "body", {}, {})

        self.assertEqual(result["errors"], [])
        self.assertEqual(result["missing_tokens"], [])
        self.assertEqual(result["user_count"], 1)
        self.assertEqual(result["email_count"], 1)

    @patch(PATCH_ENUM)
    def test_unknown_token_in_missing_tokens(self, mock_enum):
        self._patch_nv_objects([])
        mock_enum.return_value = []
        result = validate_campaign("Hi {{bogus}}", "body", {}, {})
        self.assertIn("bogus", result["missing_tokens"])

    @patch(PATCH_ENUM)
    def test_manual_var_empty_value_reported(self, mock_enum):
        var = self._make_var("date", source="manual", value="", is_required=True)
        self._patch_nv_objects([var])

        user = self._make_user(1, "bob", "bob@test.com")
        mock_enum.return_value = [(user, None, None)]

        result = validate_campaign("Due {{date}}", "body", {}, {})
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["token"], "date")

    @patch(PATCH_ENUM)
    def test_query_var_resolution_failure_reported(self, mock_enum):
        var = self._make_var("title", resolver_key="project.title")
        self._patch_nv_objects([var])

        user = self._make_user(1, "carol", "carol@test.com")
        mock_enum.return_value = [(user, None, None)]

        with patch(self.PATCH_RESOLVE, side_effect=MissingValue()):
            result = validate_campaign("Re: {{title}}", "body", {}, {})

        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("project.title", result["errors"][0]["reason"])

    @patch(PATCH_ENUM)
    def test_multi_email_users_reported(self, mock_enum):
        var = self._make_var("ptitle", resolver_key="project.title")
        self._patch_nv_objects([var])

        user = self._make_user(1, "alice", "alice@test.com")
        proj1, proj2 = MagicMock(), MagicMock()
        mock_enum.return_value = [(user, proj1, None), (user, proj2, None)]

        with patch(self.PATCH_RESOLVE, return_value="Proj"):
            result = validate_campaign("{{ptitle}}", "body", {}, {})

        self.assertEqual(result["email_count"], 2)
        self.assertEqual(result["user_count"], 1)
        self.assertEqual(len(result["multi_emails"]), 1)
        self.assertEqual(result["multi_emails"][0]["username"], "alice")
        self.assertEqual(result["multi_emails"][0]["count"], 2)

    @patch(PATCH_ENUM)
    def test_scope_propagated_correctly(self, mock_enum):
        var = self._make_var("aid", resolver_key="allocation.id")
        self._patch_nv_objects([var])
        mock_enum.return_value = []

        result = validate_campaign("Alloc {{aid}}", "body", {}, {})
        self.assertEqual(result["scope"], "allocation")
        mock_enum.assert_called_once()
        call_args = mock_enum.call_args
        self.assertEqual(call_args[0][1], "allocation")


if __name__ == "__main__":
    unittest.main()
