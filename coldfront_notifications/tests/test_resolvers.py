"""Unit tests for coldfront_notifications.resolvers."""
import unittest
from unittest.mock import MagicMock

import coldfront_notifications.tests.django_setup  # noqa: F401

from coldfront_notifications.template_variable_value_resolver import (
    MissingValue,
    ResolverContext,
    format_value,
    registry as resolver_registry,
)


class TestFormatValue(unittest.TestCase):
    """Tests for format_value()."""

    def test_valid_string(self):
        self.assertEqual(format_value("hello"), "hello")

    def test_strips_whitespace(self):
        self.assertEqual(format_value("  world  "), "world")

    def test_none_raises_missing(self):
        with self.assertRaises(MissingValue):
            format_value(None)

    def test_empty_string_raises_missing(self):
        with self.assertRaises(MissingValue):
            format_value("")

    def test_whitespace_only_raises_missing(self):
        with self.assertRaises(MissingValue):
            format_value("   ")

    def test_coerces_to_string(self):
        self.assertEqual(format_value(42), "42")


class TestResolverContext(unittest.TestCase):
    """Tests for ResolverContext typed accessors."""

    def test_user_returns_value(self):
        context = ResolverContext({"user": "alice"})
        self.assertEqual(context.user, "alice")

    def test_user_none_raises_missing(self):
        context = ResolverContext({"user": None})
        with self.assertRaises(MissingValue):
            _ = context.user

    def test_user_missing_key_raises_missing(self):
        context = ResolverContext({})
        with self.assertRaises(MissingValue):
            _ = context.user

    def test_project_returns_value(self):
        context = ResolverContext({"project": "proj1"})
        self.assertEqual(context.project, "proj1")

    def test_project_none_raises_missing(self):
        context = ResolverContext({"project": None})
        with self.assertRaises(MissingValue):
            _ = context.project

    def test_allocation_returns_value(self):
        context = ResolverContext({"allocation": "alloc1"})
        self.assertEqual(context.allocation, "alloc1")

    def test_allocation_none_raises_missing(self):
        context = ResolverContext({"allocation": None})
        with self.assertRaises(MissingValue):
            _ = context.allocation

    def test_primary_resource_returns_parent(self):
        allocation = MagicMock()
        allocation.get_parent_resource = "StorageA"
        context = ResolverContext({"allocation": allocation})
        self.assertEqual(context.primary_resource, "StorageA")

    def test_primary_resource_none_raises_missing(self):
        allocation = MagicMock()
        allocation.get_parent_resource = None
        context = ResolverContext({"allocation": allocation})
        with self.assertRaises(MissingValue):
            _ = context.primary_resource


class TestResolve(unittest.TestCase):
    """Tests for resolver_registry.resolve()."""

    def test_valid_resolver_key_dispatches(self):
        user = MagicMock()
        user.username = "jdoe"
        context = {"user": user, "project": None, "allocation": None}
        result = resolver_registry.resolve("user.username", context)
        self.assertEqual(result, "jdoe")

    def test_unknown_key_raises_missing(self):
        context = {"user": MagicMock(), "project": None, "allocation": None}
        with self.assertRaises(MissingValue):
            resolver_registry.resolve("nonexistent.key", context)

    def test_attribute_error_raises_missing(self):
        user = MagicMock(spec=[])
        context = {"user": user, "project": None, "allocation": None}
        with self.assertRaises(MissingValue):
            resolver_registry.resolve("user.username", context)

    def test_resolve_user_email(self):
        user = MagicMock()
        user.email = "alice@example.com"
        context = {"user": user, "project": None, "allocation": None}
        self.assertEqual(resolver_registry.resolve("user.email", context), "alice@example.com")

    def test_resolve_user_full_name(self):
        user = MagicMock()
        user.get_full_name.return_value = "Jane Doe"
        context = {"user": user, "project": None, "allocation": None}
        self.assertEqual(resolver_registry.resolve("user.full_name", context), "Jane Doe")

    def test_resolve_user_full_name_falls_back_to_username(self):
        user = MagicMock()
        user.get_full_name.return_value = ""
        user.username = "jdoe"
        context = {"user": user, "project": None, "allocation": None}
        self.assertEqual(resolver_registry.resolve("user.full_name", context), "jdoe")


class TestQueryChoicesConsistency(unittest.TestCase):
    """Tests that resolver_registry.choices, resolver_registry.resolvers, and resolver_registry.scopes are consistent."""

    def test_choices_keys_match_resolvers(self):
        choice_keys = {key for key, _label, _group in resolver_registry.choices}
        resolver_keys = set(resolver_registry.resolvers.keys())
        self.assertEqual(choice_keys, resolver_keys)

    def test_scopes_maps_every_choice(self):
        choice_keys = {key for key, _label, _group in resolver_registry.choices}
        scope_keys = set(resolver_registry.scopes.keys())
        self.assertEqual(choice_keys, scope_keys)

    def test_all_scopes_are_valid(self):
        valid_scopes = {"user", "project", "allocation"}
        for key, scope in resolver_registry.scopes.items():
            self.assertIn(scope, valid_scopes, f"invalid scope for {key}")


class TestScopeClassification(unittest.TestCase):
    """Tests that resolver keys map to the correct scope."""

    def test_user_scope(self):
        self.assertEqual(resolver_registry.scopes["user.username"], "user")
        self.assertEqual(resolver_registry.scopes["user.email"], "user")

    def test_project_scope(self):
        self.assertEqual(resolver_registry.scopes["project.title"], "project")
        self.assertEqual(resolver_registry.scopes["project.pi.email"], "project")
        self.assertEqual(resolver_registry.scopes["project.user_count"], "project")
        self.assertEqual(resolver_registry.scopes["project.usernames"], "project")
        self.assertEqual(resolver_registry.scopes["projectuser.role.name"], "project")

    def test_allocation_scope(self):
        self.assertEqual(resolver_registry.scopes["allocation.path"], "allocation")
        self.assertEqual(resolver_registry.scopes["allocation.quota"], "allocation")
        self.assertEqual(resolver_registry.scopes["allocation.usage"], "allocation")
        self.assertEqual(resolver_registry.scopes["allocation.status.name"], "allocation")

    def test_resource_scope(self):
        self.assertEqual(resolver_registry.scopes["resource.name"], "allocation")


class TestResolveExceptionHandling(unittest.TestCase):

    def test_arbitrary_exception_becomes_missing_value(self):
        user = MagicMock()
        type(user).username = property(lambda self: (_ for _ in ()).throw(TypeError("boom")))
        context = {"user": user, "project": None, "allocation": None}
        with self.assertRaises(MissingValue):
            resolver_registry.resolve("user.username", context)


class TestProjectResolvers(unittest.TestCase):
    """Tests for project-scoped resolvers."""

    def _make_context(self, user=None, project=None, allocation=None):
        return {
            "user": user or MagicMock(),
            "project": project,
            "allocation": allocation,
        }

    def test_project_user_count(self):
        project = MagicMock()
        active_users_qs = MagicMock()
        active_users_qs.count.return_value = 5
        project.projectuser_set.filter.return_value = active_users_qs
        context = self._make_context(project=project)
        self.assertEqual(resolver_registry.resolve("project.user_count", context), "5")
        project.projectuser_set.filter.assert_called_once_with(status__name="Active")

    def test_project_user_count_zero_returns_zero(self):
        project = MagicMock()
        active_users_qs = MagicMock()
        active_users_qs.count.return_value = 0
        project.projectuser_set.filter.return_value = active_users_qs
        context = self._make_context(project=project)
        self.assertEqual(resolver_registry.resolve("project.user_count", context), "0")

    def test_project_user_count_no_project_raises(self):
        context = self._make_context(project=None)
        with self.assertRaises(MissingValue):
            resolver_registry.resolve("project.user_count", context)

    def test_project_usernames(self):
        project = MagicMock()
        queryset = MagicMock()
        queryset.order_by.return_value = queryset
        queryset.values_list.return_value = ["alice", "bob", "carol"]
        project.projectuser_set.filter.return_value = queryset
        context = self._make_context(project=project)
        self.assertEqual(resolver_registry.resolve("project.usernames", context), "alice, bob, carol")

    def test_project_usernames_empty_raises(self):
        project = MagicMock()
        queryset = MagicMock()
        queryset.order_by.return_value = queryset
        queryset.values_list.return_value = []
        project.projectuser_set.filter.return_value = queryset
        context = self._make_context(project=project)
        with self.assertRaises(MissingValue):
            resolver_registry.resolve("project.usernames", context)


class TestAllocationResolvers(unittest.TestCase):
    """Tests for allocation-scoped resolvers."""

    def _make_context(self, user=None, project=None, allocation=None):
        return {
            "user": user or MagicMock(),
            "project": project,
            "allocation": allocation,
        }

    def test_allocation_path(self):
        allocation = MagicMock()
        allocation.path = "/n/holylfs/LABS/mylab"
        context = self._make_context(allocation=allocation)
        self.assertEqual(resolver_registry.resolve("allocation.path", context), "/n/holylfs/LABS/mylab")

    def test_allocation_path_empty_raises(self):
        allocation = MagicMock()
        allocation.path = ""
        context = self._make_context(allocation=allocation)
        with self.assertRaises(MissingValue):
            resolver_registry.resolve("allocation.path", context)

    def test_allocation_path_no_allocation_raises(self):
        context = self._make_context(allocation=None)
        with self.assertRaises(MissingValue):
            resolver_registry.resolve("allocation.path", context)

    def test_allocation_quota(self):
        allocation = MagicMock()
        allocation.get_attribute.return_value = 10.5
        context = self._make_context(allocation=allocation)
        self.assertEqual(resolver_registry.resolve("allocation.quota", context), "10.5")
        allocation.get_attribute.assert_called_once_with("Storage Quota (TB)")

    def test_allocation_quota_none_raises(self):
        allocation = MagicMock()
        allocation.get_attribute.return_value = None
        context = self._make_context(allocation=allocation)
        with self.assertRaises(MissingValue):
            resolver_registry.resolve("allocation.quota", context)

    def test_allocation_usage(self):
        allocation = MagicMock()
        allocation.usage = 7.3
        context = self._make_context(allocation=allocation)
        self.assertEqual(resolver_registry.resolve("allocation.usage", context), "7.3")

    def test_allocation_usage_none_raises(self):
        allocation = MagicMock()
        allocation.usage = None
        context = self._make_context(allocation=allocation)
        with self.assertRaises(MissingValue):
            resolver_registry.resolve("allocation.usage", context)

    def test_removed_resolvers_raise(self):
        removed_keys = (
            "user.ifxid", "allocation.id", "project.description",
            "allocation.description", "allocation.justification",
            "resource.description",
        )
        for key in removed_keys:
            context = self._make_context(project=MagicMock(), allocation=MagicMock())
            with self.assertRaises(MissingValue, msg=f"{key} should not resolve"):
                resolver_registry.resolve(key, context)


if __name__ == "__main__":
    unittest.main()
