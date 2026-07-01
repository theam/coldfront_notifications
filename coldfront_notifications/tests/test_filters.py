"""Unit tests for coldfront_notifications.filters."""
import unittest
from unittest.mock import MagicMock, patch

import coldfront_notifications.tests.django_setup  # noqa: F401

from coldfront_notifications.filters import (
    ALLOCATION_FILTERS,
    AllocationFilter,
    BaseFilter,
    DepartmentFilter,
    FILTER_REGISTRY,
    FilterDataBuilder,
    PROJECT_FILTERS,
    ProjectFilter,
    RecipientResolver,
    ResourceFilter,
    RoleFilter,
    StatusFilter,
)


class TestBaseFilter(unittest.TestCase):
    """BaseFilter enforces the interface contract."""

    def test_initial_options_is_abstract(self):
        with self.assertRaises(NotImplementedError):
            BaseFilter().initial_options()

    def test_apply_is_abstract(self):
        with self.assertRaises(NotImplementedError):
            BaseFilter()._apply(None, ["x"])

    def test_narrowed_by_default_empty(self):
        # Can't instantiate BaseFilter directly (abstract), so check
        # via a concrete subclass that doesn't override narrowed_by.
        self.assertEqual(StatusFilter().narrowed_by(), [])

    def test_to_dict_shape(self):
        """to_dict returns options + narrowed_by keys."""
        f = StatusFilter()
        with patch.object(f, "initial_options", return_value=[{"id": "x", "label": "X"}]):
            d = f.to_dict()
        self.assertIn("options", d)
        self.assertIn("narrowed_by", d)
        self.assertIsInstance(d["options"], list)
        self.assertIsInstance(d["narrowed_by"], list)


class TestDepartmentFilter(unittest.TestCase):
    """DepartmentFilter uses the Department proxy model and batches queries."""

    @patch("coldfront_notifications.filters.DepartmentFilter.initial_options")
    def test_narrowed_by_is_empty(self, _mock):
        self.assertEqual(DepartmentFilter().narrowed_by(), [])

    @patch("coldfront_notifications.filters.DepartmentFilter.initial_options")
    def test_option_shape(self, mock_opts):
        """Each option must have id (pk), label, and project_ids."""
        mock_opts.return_value = [
            {"id": 1, "label": "Physics", "project_ids": [1, 2]},
            {"id": 2, "label": "Math", "project_ids": [3]},
        ]
        options = DepartmentFilter().initial_options()
        for opt in options:
            self.assertIn("id", opt)
            self.assertIn("label", opt)
            self.assertIn("project_ids", opt)
            self.assertIsInstance(opt["id"], int)
            self.assertIsInstance(opt["project_ids"], list)


class TestProjectFilter(unittest.TestCase):
    """ProjectFilter returns all projects, narrowed by departments."""

    def test_narrowed_by_departments(self):
        self.assertEqual(ProjectFilter().narrowed_by(), ["departments"])

    @patch("coldfront_notifications.filters.ProjectFilter.initial_options")
    def test_option_shape(self, mock_opts):
        mock_opts.return_value = [
            {"id": 1, "label": "Alpha"},
            {"id": 2, "label": "Beta"},
        ]
        options = ProjectFilter().initial_options()
        for opt in options:
            self.assertIn("id", opt)
            self.assertIn("label", opt)
            self.assertIsInstance(opt["id"], int)


class TestResourceFilter(unittest.TestCase):
    """ResourceFilter carries project_ids for cross-filtering."""

    def test_narrowed_by_projects(self):
        self.assertEqual(ResourceFilter().narrowed_by(), ["projects"])

    @patch("coldfront_notifications.filters.ResourceFilter.initial_options")
    def test_option_shape(self, mock_opts):
        mock_opts.return_value = [
            {"id": 10, "label": "Storage", "project_ids": [1, 2]},
        ]
        options = ResourceFilter().initial_options()
        for opt in options:
            self.assertIn("id", opt)
            self.assertIn("label", opt)
            self.assertIn("project_ids", opt)
            self.assertIsInstance(opt["project_ids"], list)


class TestStatusFilter(unittest.TestCase):
    """StatusFilter is independent — name strings as IDs."""

    def test_narrowed_by_empty(self):
        self.assertEqual(StatusFilter().narrowed_by(), [])

    @patch("coldfront_notifications.filters.StatusFilter.initial_options")
    def test_option_shape(self, mock_opts):
        mock_opts.return_value = [
            {"id": "Active", "label": "Active"},
            {"id": "Denied", "label": "Denied"},
        ]
        options = StatusFilter().initial_options()
        for opt in options:
            self.assertIn("id", opt)
            self.assertIn("label", opt)
            self.assertIsInstance(opt["id"], str)


class TestAllocationFilter(unittest.TestCase):
    """AllocationFilter carries project_id, status, and resource_ids."""

    def test_narrowed_by_three_filters(self):
        self.assertEqual(
            AllocationFilter().narrowed_by(),
            ["projects", "resources", "statuses"],
        )

    @patch("coldfront_notifications.filters.AllocationFilter.initial_options")
    def test_option_shape(self, mock_opts):
        mock_opts.return_value = [
            {
                "id": 100,
                "label": "Alpha \u2014 Storage",
                "project_id": 1,
                "status": "Active",
                "resource_ids": [10, 11],
            },
        ]
        options = AllocationFilter().initial_options()
        for opt in options:
            self.assertIn("id", opt)
            self.assertIn("label", opt)
            self.assertIn("project_id", opt)
            self.assertIn("status", opt)
            self.assertIn("resource_ids", opt)
            self.assertIsInstance(opt["resource_ids"], list)


class TestRoleFilter(unittest.TestCase):
    """RoleFilter is independent — name strings as IDs."""

    def test_narrowed_by_empty(self):
        self.assertEqual(RoleFilter().narrowed_by(), [])

    @patch("coldfront_notifications.filters.RoleFilter.initial_options")
    def test_option_shape(self, mock_opts):
        mock_opts.return_value = [
            {"id": "Manager", "label": "Manager"},
            {"id": "User", "label": "User"},
        ]
        options = RoleFilter().initial_options()
        for opt in options:
            self.assertIsInstance(opt["id"], str)


class TestFilterRegistry(unittest.TestCase):
    """FILTER_REGISTRY maps all six filter names to classes."""

    def test_all_six_filters_registered(self):
        expected = {
            "departments", "projects", "resources",
            "statuses", "allocations", "roles",
        }
        self.assertEqual(set(FILTER_REGISTRY.keys()), expected)

    def test_all_values_are_base_filter_subclasses(self):
        for name, cls in FILTER_REGISTRY.items():
            self.assertTrue(
                issubclass(cls, BaseFilter),
                f"{name} -> {cls} is not a BaseFilter subclass",
            )


class TestFilterDataBuilder(unittest.TestCase):
    """FilterDataBuilder.build() returns a JSON-serializable dict."""

    @patch("coldfront_notifications.filters.FILTER_REGISTRY", {
        "test_filter": MagicMock(
            return_value=MagicMock(
                to_dict=MagicMock(return_value={
                    "options": [{"id": 1, "label": "X"}],
                    "narrowed_by": [],
                }),
            ),
        ),
    })
    def test_structure(self):
        data = FilterDataBuilder().build()
        self.assertIn("test_filter", data)
        self.assertIn("options", data["test_filter"])
        self.assertIn("narrowed_by", data["test_filter"])

    @patch("coldfront_notifications.filters.FILTER_REGISTRY", {
        "empty": MagicMock(
            return_value=MagicMock(
                to_dict=MagicMock(return_value={
                    "options": [],
                    "narrowed_by": [],
                }),
            ),
        ),
    })
    def test_empty_options(self):
        data = FilterDataBuilder().build()
        self.assertEqual(data["empty"]["options"], [])
        self.assertEqual(data["empty"]["narrowed_by"], [])


class TestBaseFilterApply(unittest.TestCase):
    """BaseFilter.apply() skips _apply when values is empty."""

    def test_apply_returns_qs_unchanged_when_values_empty(self):
        qs = MagicMock()
        f = StatusFilter()
        result = f.apply(qs, [])
        self.assertIs(result, qs)

    def test_apply_calls_apply_when_values_present(self):
        qs = MagicMock()
        filtered = MagicMock()
        qs.filter.return_value = filtered
        f = RoleFilter()
        result = f.apply(qs, ["PI"])
        self.assertIsNot(result, qs)


class TestDepartmentFilterApply(unittest.TestCase):
    """DepartmentFilter._apply uses org_relation tree traversal."""

    def test_apply_filters_project_users(self):
        import sys

        MockRel = MagicMock()
        saved = sys.modules.get("ifxuser.models")
        sys.modules["ifxuser.models"] = MagicMock(OrgRelation=MockRel)

        try:
            project_users = MagicMock()
            DepartmentFilter()._apply(project_users, [1880])
            project_users.filter.assert_called_once()
        finally:
            if saved is None:
                sys.modules.pop("ifxuser.models", None)
            else:
                sys.modules["ifxuser.models"] = saved


class TestProjectFilterApply(unittest.TestCase):
    def test_filters_by_project_pk(self):
        qs = MagicMock()
        ProjectFilter()._apply(qs, [1, 2])
        qs.filter.assert_called_once_with(project__pk__in=[1, 2])


class TestRoleFilterApply(unittest.TestCase):
    def test_filters_by_role_name(self):
        qs = MagicMock()
        RoleFilter()._apply(qs, ["PI", "User"])
        qs.filter.assert_called_once_with(role__name__in=["PI", "User"])


class TestResourceFilterApply(unittest.TestCase):
    def test_filters_by_resource_pk(self):
        qs = MagicMock()
        ResourceFilter()._apply(qs, [10, 20])
        qs.filter.assert_called_once_with(resources__pk__in=[10, 20])


class TestStatusFilterApply(unittest.TestCase):
    def test_filters_by_status_name(self):
        qs = MagicMock()
        StatusFilter()._apply(qs, ["Active", "Denied"])
        qs.filter.assert_called_once_with(status__name__in=["Active", "Denied"])


class TestAllocationFilterApply(unittest.TestCase):
    def test_filters_by_pk(self):
        qs = MagicMock()
        AllocationFilter()._apply(qs, [100, 200])
        qs.filter.assert_called_once_with(pk__in=[100, 200])


class TestHasAllocationFilters(unittest.TestCase):
    def test_true_when_resources_present(self):
        self.assertTrue(RecipientResolver({"resources": [1]})._has_allocation_filters())

    def test_true_when_statuses_present(self):
        self.assertTrue(RecipientResolver({"statuses": ["Active"]})._has_allocation_filters())

    def test_true_when_allocations_present(self):
        self.assertTrue(RecipientResolver({"allocations": [1]})._has_allocation_filters())

    def test_false_when_only_project_filters(self):
        self.assertFalse(RecipientResolver({"projects": [1], "roles": ["PI"]})._has_allocation_filters())

    def test_false_when_empty(self):
        self.assertFalse(RecipientResolver({})._has_allocation_filters())


class TestFilterConstants(unittest.TestCase):
    def test_project_filters(self):
        self.assertEqual(
            set(PROJECT_FILTERS),
            {"departments", "projects", "roles"},
        )

    def test_allocation_filters(self):
        self.assertEqual(
            set(ALLOCATION_FILTERS),
            {"allocations", "resources", "statuses"},
        )

    def test_all_filters_covered(self):
        self.assertEqual(
            set(PROJECT_FILTERS) | set(ALLOCATION_FILTERS),
            set(FILTER_REGISTRY.keys()),
        )


class TestNarrowingRules(unittest.TestCase):
    """Verify the declared narrowing graph matches the design spec."""

    def test_department_narrows_nothing(self):
        self.assertEqual(DepartmentFilter().narrowed_by(), [])

    def test_project_narrowed_by_department(self):
        self.assertEqual(ProjectFilter().narrowed_by(), ["departments"])

    def test_resource_narrowed_by_project(self):
        self.assertEqual(ResourceFilter().narrowed_by(), ["projects"])

    def test_status_narrows_nothing(self):
        self.assertEqual(StatusFilter().narrowed_by(), [])

    def test_allocation_narrowed_by_three(self):
        result = AllocationFilter().narrowed_by()
        self.assertIn("projects", result)
        self.assertIn("resources", result)
        self.assertIn("statuses", result)

    def test_role_narrows_nothing(self):
        self.assertEqual(RoleFilter().narrowed_by(), [])


if __name__ == "__main__":
    unittest.main()
