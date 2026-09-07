"""Unit tests for coldfront_notifications.notification_validator."""
import unittest
from unittest.mock import MagicMock, patch

import coldfront_notifications.tests.django_setup  # noqa: F401

from coldfront_notifications.template_variable_value_resolver import MissingValue
from coldfront_notifications.campaign_sender import TOKEN_PATTERN as TASKS_TOKEN_RE
from coldfront_notifications.notification_validator import (
    TOKEN_PATTERN as VALIDATORS_TOKEN_RE,
    NotificationValidator,
    extract_tokens,
    determine_scope,
)


class TestExtractTokens(unittest.TestCase):
    """Tests for extract_tokens()."""

    def test_no_tokens(self):
        self.assertEqual(extract_tokens("Hello world", "No tokens here"), [])

    def test_one_token_in_subject(self):
        self.assertEqual(extract_tokens("Hello {{name}}", "body"), ["name"])

    def test_one_token_in_body(self):
        self.assertEqual(extract_tokens("subj", "Hello {{name}}"), ["name"])

    def test_duplicates_deduped_order_preserved(self):
        result = extract_tokens("{{a}} {{b}} {{a}}", "{{b}} {{c}}")
        self.assertEqual(result, ["a", "b", "c"])

    def test_nested_braces_only_valid_matches(self):
        result = extract_tokens("{{{foo}}}", "")
        self.assertEqual(result, ["foo"])

    def test_invalid_tokens_ignored(self):
        result = extract_tokens("{{hello world}} {{good}}", "")
        self.assertEqual(result, ["good"])

    def test_empty_strings(self):
        self.assertEqual(extract_tokens("", ""), [])

    def test_none_subject(self):
        self.assertEqual(extract_tokens(None, "{{x}}"), ["x"])

    def test_multiple_tokens(self):
        result = extract_tokens("{{first}} {{last}}", "Dear {{first}}")
        self.assertEqual(result, ["first", "last"])


class TestDetermineScope(unittest.TestCase):
    """Tests for determine_scope()."""

    def _make_var(self, resolver_key, source="query"):
        variable = MagicMock()
        variable.source = "query"
        variable.Source.QUERY = "query"
        variable.resolver_key = resolver_key
        return variable

    def _make_manual_var(self):
        variable = MagicMock()
        variable.source = "manual"
        variable.Source.QUERY = "query"
        variable.resolver_key = None
        return variable

    def test_no_vars_returns_user(self):
        self.assertEqual(determine_scope([]), "user")

    def test_only_user_vars_returns_user(self):
        variables = [self._make_var("user.email"), self._make_var("user.username")]
        self.assertEqual(determine_scope(variables), "user")

    def test_project_vars_returns_project(self):
        variables = [self._make_var("project.title")]
        self.assertEqual(determine_scope(variables), "project")

    def test_allocation_vars_returns_allocation(self):
        variables = [self._make_var("allocation.status.name")]
        self.assertEqual(determine_scope(variables), "allocation")

    def test_resource_vars_returns_allocation(self):
        variables = [self._make_var("resource.name")]
        self.assertEqual(determine_scope(variables), "allocation")

    def test_mix_highest_wins_allocation(self):
        variables = [
            self._make_var("user.email"),
            self._make_var("project.title"),
            self._make_var("allocation.status.name"),
        ]
        self.assertEqual(determine_scope(variables), "allocation")

    def test_mix_project_beats_user(self):
        variables = [
            self._make_var("user.email"),
            self._make_var("project.title"),
        ]
        self.assertEqual(determine_scope(variables), "project")

    def test_manual_vars_dont_affect_scope(self):
        variables = [self._make_manual_var()]
        self.assertEqual(determine_scope(variables), "user")

    def test_manual_with_allocation_still_allocation(self):
        variables = [self._make_manual_var(), self._make_var("allocation.status.name")]
        self.assertEqual(determine_scope(variables), "allocation")


class TestTokenRegex(unittest.TestCase):
    """Tests for the TOKEN_PATTERN used across modules."""

    def test_matches_simple_token(self):
        match = TASKS_TOKEN_RE.search("{{foo}}")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "foo")

    def test_matches_underscore_token(self):
        match = TASKS_TOKEN_RE.search("{{first_name}}")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "first_name")

    def test_no_match_spaces(self):
        match = TASKS_TOKEN_RE.search("{{ foo }}")
        self.assertIsNone(match)

    def test_no_match_special_chars(self):
        match = TASKS_TOKEN_RE.search("{{foo-bar}}")
        self.assertIsNone(match)

    def test_validators_and_tasks_same_pattern(self):
        self.assertEqual(TASKS_TOKEN_RE.pattern, VALIDATORS_TOKEN_RE.pattern)


class TestNotificationValidator(unittest.TestCase):

    PATCH_RESOLVER = "coldfront_notifications.notification_validator.RecipientResolver"
    PATCH_RESOLVE = "coldfront_notifications.notification_validator.resolver_registry.resolve"

    def _patch_nv_objects(self, return_value):
        patcher = patch(
            "coldfront_notifications.models.NotificationVariable.objects"
        )
        mock_objects = patcher.start()
        mock_objects.filter.return_value = return_value
        self.addCleanup(patcher.stop)

    def _make_var(self, key, source="query", resolver_key="user.email",
                  value="", is_required=True):
        variable = MagicMock()
        variable.key = key
        variable.source = source
        variable.Source.QUERY = "query"
        variable.Source.MANUAL = "manual"
        variable.resolver_key = resolver_key
        variable.value = value
        variable.is_required = is_required
        return variable

    def _make_user(self, pk, username, email):
        user = MagicMock()
        user.pk = pk
        user.username = username
        user.email = email
        return user

    def _patch_recipient_resolver(self, tuples):
        mock_resolver_instance = MagicMock()
        mock_resolver_instance.enumerate_deduped.return_value = iter(tuples)
        patcher = patch(
            self.PATCH_RESOLVER,
            return_value=mock_resolver_instance,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_all_tokens_resolve_cleanly(self):
        variable = self._make_var("name", resolver_key="user.full_name")
        self._patch_nv_objects([variable])

        user = self._make_user(1, "alice", "alice@test.com")
        self._patch_recipient_resolver([(user, None, None)])

        with patch(self.PATCH_RESOLVE, return_value="Alice"):
            result = NotificationValidator("Hi {{name}}", "body", {}).validate()

        self.assertEqual(result["errors"], [])
        self.assertEqual(result["missing_tokens"], [])
        self.assertEqual(result["user_count"], 1)
        self.assertEqual(result["email_count"], 1)

    def test_unknown_token_in_missing_tokens(self):
        self._patch_nv_objects([])
        self._patch_recipient_resolver([])
        result = NotificationValidator("Hi {{bogus}}", "body", {}).validate()
        self.assertIn("bogus", result["missing_tokens"])

    def test_manual_var_empty_value_reported(self):
        variable = self._make_var("date", source="manual", value="", is_required=True)
        self._patch_nv_objects([variable])

        user = self._make_user(1, "bob", "bob@test.com")
        self._patch_recipient_resolver([(user, None, None)])

        result = NotificationValidator("Due {{date}}", "body", {}).validate()
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["token"], "date")

    def test_query_var_resolution_failure_reported(self):
        variable = self._make_var("title", resolver_key="project.title")
        self._patch_nv_objects([variable])

        user = self._make_user(1, "carol", "carol@test.com")
        self._patch_recipient_resolver([(user, None, None)])

        with patch(self.PATCH_RESOLVE, side_effect=MissingValue()):
            result = NotificationValidator("Re: {{title}}", "body", {}).validate()

        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("project.title", result["errors"][0]["reason"])

    def test_multi_email_users_reported(self):
        variable = self._make_var("ptitle", resolver_key="project.title")
        self._patch_nv_objects([variable])

        user = self._make_user(1, "alice", "alice@test.com")
        project_1, project_2 = MagicMock(), MagicMock()
        self._patch_recipient_resolver([(user, project_1, None), (user, project_2, None)])

        with patch(self.PATCH_RESOLVE, return_value="Proj"):
            result = NotificationValidator("{{ptitle}}", "body", {}).validate()

        self.assertEqual(result["email_count"], 2)
        self.assertEqual(result["user_count"], 1)
        self.assertEqual(len(result["multi_emails"]), 1)
        self.assertEqual(result["multi_emails"][0]["username"], "alice")
        self.assertEqual(result["multi_emails"][0]["count"], 2)

    def test_scope_propagated_correctly(self):
        variable = self._make_var("aid", resolver_key="allocation.status.name")
        self._patch_nv_objects([variable])
        self._patch_recipient_resolver([])

        result = NotificationValidator("Alloc {{aid}}", "body", {}).validate()
        self.assertEqual(result["scope"], "allocation")


class TestNotificationValidatorDirectMode(unittest.TestCase):
    """NotificationValidator with direct user selection."""

    PATCH_RESOLVER = "coldfront_notifications.notification_validator.RecipientResolver"
    PATCH_RESOLVE = "coldfront_notifications.notification_validator.resolver_registry.resolve"

    def _patch_nv_objects(self, return_value):
        patcher = patch(
            "coldfront_notifications.models.NotificationVariable.objects"
        )
        mock_objects = patcher.start()
        mock_objects.filter.return_value = return_value
        self.addCleanup(patcher.stop)

    def _make_var(self, key, source="query", resolver_key="user.email",
                  value="", is_required=True):
        variable = MagicMock()
        variable.key = key
        variable.source = source
        variable.Source.QUERY = "query"
        variable.Source.MANUAL = "manual"
        variable.resolver_key = resolver_key
        variable.value = value
        variable.is_required = is_required
        return variable

    def _make_user(self, pk, username, email):
        user = MagicMock()
        user.pk = pk
        user.username = username
        user.email = email
        user.get_full_name.return_value = username
        return user

    def _patch_recipient_resolver(self, tuples):
        mock_resolver_instance = MagicMock()
        mock_resolver_instance.enumerate_deduped.return_value = iter(tuples)
        patcher = patch(
            self.PATCH_RESOLVER,
            return_value=mock_resolver_instance,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_direct_mode_user_scope_no_tokens(self):
        """With no template variables, scope stays 'user' — one email per user."""
        self._patch_nv_objects([])

        user1 = self._make_user(1, "alice", "alice@test.com")
        user2 = self._make_user(2, "bob", "bob@test.com")
        self._patch_recipient_resolver([
            (user1, None, None),
            (user2, None, None),
        ])

        filters = {"selection_mode": "direct", "direct_user_pks": [1, 2]}
        result = NotificationValidator("Hello", "body", filters).validate()

        self.assertEqual(result["scope"], "user")
        self.assertEqual(result["user_count"], 2)
        self.assertEqual(result["email_count"], 2)
        self.assertEqual(result["errors"], [])

    def test_direct_mode_user_scope_with_user_vars(self):
        """User-scoped variables keep scope at 'user'."""
        variable = self._make_var("name", resolver_key="user.full_name")
        self._patch_nv_objects([variable])

        user1 = self._make_user(1, "alice", "alice@test.com")
        self._patch_recipient_resolver([(user1, None, None)])

        filters = {"selection_mode": "direct", "direct_user_pks": [1]}
        with patch(self.PATCH_RESOLVE, return_value="Alice"):
            result = NotificationValidator("Hi {{name}}", "body", filters).validate()

        self.assertEqual(result["scope"], "user")
        self.assertEqual(result["user_count"], 1)
        self.assertEqual(result["email_count"], 1)

    def test_direct_mode_project_scope_with_project_vars(self):
        """Project-scoped variables elevate scope to 'project'."""
        variable = self._make_var("ptitle", resolver_key="project.title")
        self._patch_nv_objects([variable])

        user1 = self._make_user(1, "alice", "alice@test.com")
        proj1 = MagicMock(title="Alpha", pk=10)
        proj2 = MagicMock(title="Beta", pk=20)
        self._patch_recipient_resolver([
            (user1, proj1, None),
            (user1, proj2, None),
        ])

        filters = {"selection_mode": "direct", "direct_user_pks": [1]}
        with patch(self.PATCH_RESOLVE, return_value="Title"):
            result = NotificationValidator("Re: {{ptitle}}", "body", filters).validate()

        self.assertEqual(result["scope"], "project")
        self.assertEqual(result["user_count"], 1)
        self.assertEqual(result["email_count"], 2)

    def test_direct_mode_passes_filters_to_resolver(self):
        """The filters dict (with selection_mode) is passed to RecipientResolver."""
        self._patch_nv_objects([])

        filters = {"selection_mode": "direct", "direct_user_pks": [1, 2, 3]}
        with patch(self.PATCH_RESOLVER) as MockResolver:
            MockResolver.return_value.enumerate_deduped.return_value = iter([])
            NotificationValidator("subj", "body", filters).validate()
            MockResolver.assert_called_once_with(filters)


if __name__ == "__main__":
    unittest.main()
