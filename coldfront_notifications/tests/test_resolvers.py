"""Unit tests for coldfront_notifications.resolvers."""
import unittest
from unittest.mock import MagicMock

import coldfront_notifications.tests.django_setup  # noqa: F401

from coldfront_notifications.resolvers import (
    QUERY_CHOICES,
    QUERY_RESOLVERS,
    QUERY_SCOPES,
    MissingValue,
    _fmt,
    _need,
    _primary_resource,
    _scope_for,
    resolve,
)


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


class TestPrimaryResource(unittest.TestCase):

    def test_none_allocation_returns_none(self):
        self.assertIsNone(_primary_resource(None))

    def test_returns_get_parent_resource(self):
        alloc = MagicMock()
        alloc.get_parent_resource = "StorageA"
        self.assertEqual(_primary_resource(alloc), "StorageA")


class TestResolveExceptionHandling(unittest.TestCase):

    def test_arbitrary_exception_becomes_missing_value(self):
        user = MagicMock()
        type(user).username = property(lambda self: (_ for _ in ()).throw(TypeError("boom")))
        ctx = {"user": user, "project": None, "allocation": None}
        with self.assertRaises(MissingValue):
            resolve("user.username", ctx)


if __name__ == "__main__":
    unittest.main()
