"""
Integration tests for the coldfront_notifications plugin.

Requires Django's test database with ColdFront models available.
"""
import json
from datetime import timedelta
import sys
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from coldfront.core.allocation.models import (
    Allocation,
    AllocationStatusChoice,
    AllocationUser,
    AllocationUserStatusChoice,
)
from coldfront.core.field_of_science.models import FieldOfScience
from coldfront.core.project.models import (
    Project,
    ProjectStatusChoice,
    ProjectUser,
    ProjectUserRoleChoice,
    ProjectUserStatusChoice,
)
from coldfront.core.resource.models import Resource, ResourceType
from coldfront.plugins.ifx.models import ProjectOrganization
from ifxuser.models import Organization

from coldfront_notifications.models import (
    NotificationCampaign,
    NotificationLog,
    NotificationTemplate,
    NotificationVariable,
    SenderConfig,
)
from coldfront_notifications.utils import (
    build_recipient_queryset,
    enumerate_recipients,
    enumerate_recipients_deduped,
)

User = get_user_model()


def _disconnect_resource_signals():
    """Disconnect ColdFront's Resource post_save signal that calls an external API."""
    from django.db.models.signals import post_save
    from coldfront.core.resource.models import Resource
    try:
        from coldfront.plugins.ifx.models import resource_post_save
        post_save.disconnect(resource_post_save, sender=Resource)
    except (ImportError, Exception):
        pass


class NotificationIntegrationTestCase(TestCase):
    """Base setUp for all notification integration tests."""

    @classmethod
    def setUpTestData(cls):
        _disconnect_resource_signals()
        # --- Choice objects (get_or_create since migrations may seed them) ---
        cls.role_pi, _ = ProjectUserRoleChoice.objects.get_or_create(name="Principal Investigator")
        cls.role_user, _ = ProjectUserRoleChoice.objects.get_or_create(name="User")
        cls.role_manager, _ = ProjectUserRoleChoice.objects.get_or_create(name="Manager")

        cls.pu_status_active, _ = ProjectUserStatusChoice.objects.get_or_create(name="Active")
        cls.proj_status_active, _ = ProjectStatusChoice.objects.get_or_create(name="Active")

        cls.alloc_status_active, _ = AllocationStatusChoice.objects.get_or_create(name="Active")
        cls.alloc_status_expired, _ = AllocationStatusChoice.objects.get_or_create(name="Expired")

        cls.au_status_active, _ = AllocationUserStatusChoice.objects.get_or_create(name="Active")

        cls.fos, _ = FieldOfScience.objects.get_or_create(
            description="Computer Science",
            defaults={"is_selectable": True},
        )

        cls.resource_type, _ = ResourceType.objects.get_or_create(
            name="Storage",
            defaults={"description": "Storage resource type"},
        )

        # --- Users ---
        cls.user1 = User.objects.create_user(
            username="user1", email="user1@example.com", password="testpass123",
            first_name="Alice", last_name="Smith",
        )
        cls.user2 = User.objects.create_user(
            username="user2", email="user2@example.com", password="testpass123",
            first_name="Bob", last_name="Jones",
        )
        cls.user3 = User.objects.create_user(
            username="user3", email="user3@example.com", password="testpass123",
            first_name="Carol", last_name="Williams",
        )
        # Make user1 staff so they can access all views
        cls.user1.is_staff = True
        cls.user1.is_superuser = True
        cls.user1.save()

        # --- Projects ---
        cls.proj1 = Project.objects.create(
            title="Project Alpha",
            description="Test project 1",
            pi=cls.user1,
            status=cls.proj_status_active,
            field_of_science=cls.fos,
        )
        cls.proj2 = Project.objects.create(
            title="Project Beta",
            description="Test project 2",
            pi=cls.user1,
            status=cls.proj_status_active,
            field_of_science=cls.fos,
        )

        # --- ProjectUser links ---
        # user1 is PI on both projects
        cls.pu1_p1 = ProjectUser.objects.create(
            project=cls.proj1, user=cls.user1,
            role=cls.role_pi, status=cls.pu_status_active,
        )
        cls.pu1_p2 = ProjectUser.objects.create(
            project=cls.proj2, user=cls.user1,
            role=cls.role_pi, status=cls.pu_status_active,
        )
        # user2 on proj1 as User
        cls.pu2_p1 = ProjectUser.objects.create(
            project=cls.proj1, user=cls.user2,
            role=cls.role_user, status=cls.pu_status_active,
        )
        # user3 on proj2 as Manager
        cls.pu3_p2 = ProjectUser.objects.create(
            project=cls.proj2, user=cls.user3,
            role=cls.role_manager, status=cls.pu_status_active,
        )

        # --- Resource ---
        cls.resource = Resource.objects.create(
            name="Test Storage",
            resource_type=cls.resource_type,
            is_allocatable=True,
        )

        # --- Allocations ---
        cls.alloc1 = Allocation.objects.create(
            project=cls.proj1,
            status=cls.alloc_status_active,
            justification="Need storage for proj1",
        )
        cls.alloc1.resources.add(cls.resource)

        cls.alloc2 = Allocation.objects.create(
            project=cls.proj2,
            status=cls.alloc_status_active,
            justification="Need storage for proj2",
        )
        cls.alloc2.resources.add(cls.resource)

        # --- AllocationUser links ---
        cls.au1_a1 = AllocationUser.objects.create(
            allocation=cls.alloc1, user=cls.user1, status=cls.au_status_active,
        )
        cls.au2_a1 = AllocationUser.objects.create(
            allocation=cls.alloc1, user=cls.user2, status=cls.au_status_active,
        )
        cls.au1_a2 = AllocationUser.objects.create(
            allocation=cls.alloc2, user=cls.user1, status=cls.au_status_active,
        )
        cls.au3_a2 = AllocationUser.objects.create(
            allocation=cls.alloc2, user=cls.user3, status=cls.au_status_active,
        )

        # --- Department (Organization) + ProjectOrganization ---
        cls.dept = Organization.objects.create(
            name="Engineering Dept",
            rank="department",
            org_tree="Test",
        )
        cls.proj_org = ProjectOrganization.objects.create(
            project=cls.proj1,
            organization=cls.dept,
        )

        # --- NotificationVariables ---
        cls.var_query = NotificationVariable.objects.create(
            key="project_title",
            label="Project Title",
            source=NotificationVariable.SOURCE_QUERY,
            resolver_key="project.title",
        )
        cls.var_manual = NotificationVariable.objects.create(
            key="maint_date",
            label="Maintenance Date",
            source=NotificationVariable.SOURCE_MANUAL,
            value="2026-05-01",
            input_widget=NotificationVariable.WIDGET_DATE,
        )

        # --- NotificationTemplate ---
        cls.template = NotificationTemplate.objects.create(
            name="Test Template",
            slug="test-template",
            subject="Update for {{project_title}}",
            body="Hello, maintenance on {{maint_date}} for {{project_title}}.",
        )

        # --- SenderConfig ---
        cls.sender_config = SenderConfig.objects.create(
            label="RC Help",
            email="rchelp@example.com",
            is_default=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user1)


# =============================================================================
# UTILS TESTS
# =============================================================================

class BuildRecipientQuerysetTest(NotificationIntegrationTestCase):
    """Tests for build_recipient_queryset."""

    def test_no_filters_returns_all_active_users(self):
        qs = build_recipient_queryset({})
        user_pks = set(qs.values_list("pk", flat=True))
        # All 3 users have active ProjectUser memberships
        self.assertIn(self.user1.pk, user_pks)
        self.assertIn(self.user2.pk, user_pks)
        self.assertIn(self.user3.pk, user_pks)

    def test_project_filter(self):
        qs = build_recipient_queryset({"projects": [self.proj1.pk]})
        user_pks = set(qs.values_list("pk", flat=True))
        # proj1 has user1 (PI) and user2 (User)
        self.assertIn(self.user1.pk, user_pks)
        self.assertIn(self.user2.pk, user_pks)
        self.assertNotIn(self.user3.pk, user_pks)

    def test_role_filter(self):
        qs = build_recipient_queryset({"roles": ["Principal Investigator"]})
        user_pks = set(qs.values_list("pk", flat=True))
        # Only user1 is PI
        self.assertIn(self.user1.pk, user_pks)
        self.assertNotIn(self.user2.pk, user_pks)
        self.assertNotIn(self.user3.pk, user_pks)

    def test_department_filter(self):
        qs = build_recipient_queryset({"departments": ["Engineering Dept"]})
        user_pks = set(qs.values_list("pk", flat=True))
        # Only proj1 linked to dept → user1, user2
        self.assertIn(self.user1.pk, user_pks)
        self.assertIn(self.user2.pk, user_pks)
        self.assertNotIn(self.user3.pk, user_pks)

    def test_allocation_status_filter(self):
        qs = build_recipient_queryset({"statuses": ["Active"]})
        user_pks = set(qs.values_list("pk", flat=True))
        # All users have active AllocationUser records
        self.assertIn(self.user1.pk, user_pks)
        self.assertIn(self.user2.pk, user_pks)
        self.assertIn(self.user3.pk, user_pks)

    def test_allocation_status_filter_expired_returns_none(self):
        qs = build_recipient_queryset({"statuses": ["Expired"]})
        self.assertEqual(qs.count(), 0)


class EnumerateRecipientsTest(NotificationIntegrationTestCase):
    """Tests for enumerate_recipients."""

    def test_scope_user_returns_distinct_users(self):
        results = list(enumerate_recipients({}, "user"))
        users = [r[0] for r in results]
        user_pks = [u.pk for u in users]
        # Each user appears once, project and allocation are None
        self.assertEqual(len(user_pks), len(set(user_pks)))
        for user, project, allocation in results:
            self.assertIsNone(project)
            self.assertIsNone(allocation)

    def test_scope_project_returns_per_project_user(self):
        results = list(enumerate_recipients({}, "project"))
        # user1 on 2 projects, user2 on 1, user3 on 1 → 4 tuples
        self.assertEqual(len(results), 4)
        for user, project, allocation in results:
            self.assertIsNotNone(project)
            self.assertIsNone(allocation)

    def test_scope_allocation_returns_per_allocation_user(self):
        results = list(enumerate_recipients({}, "allocation"))
        # alloc1: user1, user2; alloc2: user1, user3 → 4 tuples
        self.assertEqual(len(results), 4)
        for user, project, allocation in results:
            self.assertIsNotNone(project)
            self.assertIsNotNone(allocation)

    def test_scope_project_with_project_filter(self):
        results = list(enumerate_recipients({"projects": [self.proj1.pk]}, "project"))
        # proj1 has user1 and user2
        self.assertEqual(len(results), 2)
        user_pks = {r[0].pk for r in results}
        self.assertEqual(user_pks, {self.user1.pk, self.user2.pk})


class EnumerateRecipientsDedupedTest(NotificationIntegrationTestCase):
    """Tests for enumerate_recipients_deduped."""

    def test_deduped_user_gets_only_first_tuple(self):
        # user1 appears on both projects; with dedup they get only 1 tuple
        results = list(enumerate_recipients_deduped(
            {}, "project", dedupe_users=["user1"]
        ))
        user1_tuples = [r for r in results if r[0].pk == self.user1.pk]
        self.assertEqual(len(user1_tuples), 1)

    def test_non_deduped_users_unchanged(self):
        results = list(enumerate_recipients_deduped(
            {}, "project", dedupe_users=["user1"]
        ))
        # user2 and user3 still have their single tuples
        user2_tuples = [r for r in results if r[0].pk == self.user2.pk]
        user3_tuples = [r for r in results if r[0].pk == self.user3.pk]
        self.assertEqual(len(user2_tuples), 1)
        self.assertEqual(len(user3_tuples), 1)

    def test_empty_dedupe_list_is_passthrough(self):
        full = list(enumerate_recipients({}, "project"))
        deduped = list(enumerate_recipients_deduped({}, "project", []))
        self.assertEqual(len(full), len(deduped))


# =============================================================================
# VIEWS TESTS
# =============================================================================

class ViewGetEndpointsTest(NotificationIntegrationTestCase):
    """Test that GET endpoints return 200."""

    def test_dashboard(self):
        resp = self.client.get(reverse("notifications:dashboard"))
        self.assertEqual(resp.status_code, 200)

    def test_compose(self):
        resp = self.client.get(reverse("notifications:compose"))
        self.assertEqual(resp.status_code, 200)

    def test_campaign_list(self):
        resp = self.client.get(reverse("notifications:campaign-list"))
        self.assertEqual(resp.status_code, 200)

    def test_template_list(self):
        resp = self.client.get(reverse("notifications:template-list"))
        self.assertEqual(resp.status_code, 200)

    def test_variable_list(self):
        resp = self.client.get(reverse("notifications:variable-list"))
        self.assertEqual(resp.status_code, 200)

    def test_settings(self):
        resp = self.client.get(reverse("notifications:settings"))
        self.assertEqual(resp.status_code, 200)


class TemplateViewsTest(NotificationIntegrationTestCase):
    """Tests for template CRUD views."""

    def test_create_template(self):
        resp = self.client.post(reverse("notifications:template-create"), {
            "name": "New Template",
            "subject": "Hello {{maint_date}}",
            "body": "Body with {{project_title}}",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            NotificationTemplate.objects.filter(slug="new-template").exists()
        )

    def test_soft_delete_template(self):
        t = NotificationTemplate.objects.create(
            name="To Delete", slug="to-delete", subject="S", body="B",
        )
        resp = self.client.post(reverse("notifications:template-delete", args=[t.pk]))
        self.assertEqual(resp.status_code, 302)
        t.refresh_from_db()
        self.assertTrue(t.is_deleted)

    def test_soft_deleted_template_excluded_from_list(self):
        t = NotificationTemplate.objects.create(
            name="Hidden", slug="hidden-tmpl", subject="S", body="B", is_deleted=True,
        )
        resp = self.client.get(reverse("notifications:template-list"))
        self.assertNotContains(resp, "Hidden")


class VariableViewsTest(NotificationIntegrationTestCase):
    """Tests for variable CRUD views."""

    def test_create_variable_query_source(self):
        resp = self.client.post(reverse("notifications:variable-create"), {
            "key": "user_email",
            "label": "User Email",
            "source": "query",
            "resolver_key": "user.email",
            "input_widget": "text",
            "is_required": True,
            "value": "",
            "description": "",
            "example": "",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            NotificationVariable.objects.filter(key="user_email").exists()
        )

    def test_create_variable_manual_source(self):
        resp = self.client.post(reverse("notifications:variable-create"), {
            "key": "custom_msg",
            "label": "Custom Message",
            "source": "manual",
            "resolver_key": "",
            "input_widget": "text",
            "is_required": True,
            "value": "Hello World",
            "description": "",
            "example": "",
        })
        self.assertEqual(resp.status_code, 302)
        v = NotificationVariable.objects.get(key="custom_msg")
        self.assertEqual(v.value, "Hello World")

    def test_delete_variable_get_shows_confirmation(self):
        resp = self.client.get(
            reverse("notifications:variable-delete", args=[self.var_query.pk])
        )
        self.assertEqual(resp.status_code, 200)
        # The template uses this variable, so it should appear in affected
        self.assertContains(resp, "Test Template")

    def test_delete_variable_post_soft_deletes(self):
        v = NotificationVariable.objects.create(
            key="temp_var", label="Temp", source="manual", value="x",
        )
        resp = self.client.post(
            reverse("notifications:variable-delete", args=[v.pk])
        )
        self.assertEqual(resp.status_code, 302)
        v.refresh_from_db()
        self.assertTrue(v.is_deleted)

    def test_soft_deleted_variable_excluded_from_list(self):
        NotificationVariable.objects.create(
            key="gone_var", label="Gone", source="manual", value="x", is_deleted=True,
        )
        resp = self.client.get(reverse("notifications:variable-list"))
        self.assertNotContains(resp, "gone_var")


class FilterOptionsViewTest(NotificationIntegrationTestCase):
    """Tests for the event-driven filter-options AJAX endpoint."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Extra fixtures for richer cascade testing.
        cls.resource2 = Resource.objects.create(
            name="GPU Cluster",
            resource_type=cls.resource_type,
            is_allocatable=True,
        )
        # An expired allocation on proj2 with resource2.
        cls.alloc3 = Allocation.objects.create(
            project=cls.proj2,
            status=cls.alloc_status_expired,
            justification="Old GPU allocation",
        )
        cls.alloc3.resources.add(cls.resource2)

    def _post_event(self, event, selections=None):
        """Helper: POST an event to filter-options and return parsed JSON."""
        sel = {
            "projects": [], "allocations": [], "departments": [],
            "resources": [], "statuses": [], "roles": [],
        }
        if selections:
            sel.update(selections)
        resp = self.client.post(
            reverse("notifications:filter-options"),
            json.dumps({"event": event, "selections": sel}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def _ids(self, data, key):
        """Extract option ids from the response for a given filter key."""
        return [o["id"] for o in data[key]["options"]]

    # ── Response shape ──────────────────────────────────────────────
    def test_response_contains_all_six_filters(self):
        data = self._post_event("ROLE_UPDATED")
        for key in ("projects", "allocations", "departments",
                    "resources", "statuses", "roles"):
            self.assertIn(key, data)
            self.assertIn("options", data[key])
            self.assertIn("selected", data[key])

    # ── PROJECT_UPDATED ─────────────────────────────────────────────
    def test_project_updated_narrows_allocations(self):
        data = self._post_event("PROJECT_UPDATED", {
            "projects": [self.proj1.pk],
        })
        alloc_ids = self._ids(data, "allocations")
        self.assertIn(self.alloc1.pk, alloc_ids)
        self.assertNotIn(self.alloc2.pk, alloc_ids)
        self.assertNotIn(self.alloc3.pk, alloc_ids)

    def test_project_updated_narrows_departments(self):
        data = self._post_event("PROJECT_UPDATED", {
            "projects": [self.proj1.pk],
        })
        dept_ids = self._ids(data, "departments")
        self.assertIn("Engineering Dept", dept_ids)

    def test_project_updated_narrows_departments_excludes_unlinked(self):
        data = self._post_event("PROJECT_UPDATED", {
            "projects": [self.proj2.pk],
        })
        # proj2 has no department link
        dept_ids = self._ids(data, "departments")
        self.assertNotIn("Engineering Dept", dept_ids)

    def test_project_updated_narrows_resources(self):
        data = self._post_event("PROJECT_UPDATED", {
            "projects": [self.proj2.pk],
        })
        res_ids = self._ids(data, "resources")
        # proj2 has alloc2 (Test Storage) and alloc3 (GPU Cluster)
        self.assertIn(self.resource.pk, res_ids)
        self.assertIn(self.resource2.pk, res_ids)

    def test_project_updated_narrows_statuses(self):
        data = self._post_event("PROJECT_UPDATED", {
            "projects": [self.proj2.pk],
        })
        status_ids = self._ids(data, "statuses")
        # proj2 has Active (alloc2) and Expired (alloc3)
        self.assertIn("Active", status_ids)
        self.assertIn("Expired", status_ids)

    def test_project_updated_narrows_roles(self):
        data = self._post_event("PROJECT_UPDATED", {
            "projects": [self.proj1.pk],
        })
        role_ids = self._ids(data, "roles")
        # proj1 has PI (user1) and User (user2), no Manager
        self.assertIn("Principal Investigator", role_ids)
        self.assertIn("User", role_ids)
        self.assertNotIn("Manager", role_ids)

    # ── DEPARTMENT_UPDATED ──────────────────────────────────────────
    def test_department_updated_narrows_projects(self):
        data = self._post_event("DEPARTMENT_UPDATED", {
            "departments": ["Engineering Dept"],
        })
        proj_ids = self._ids(data, "projects")
        self.assertIn(self.proj1.pk, proj_ids)
        self.assertNotIn(self.proj2.pk, proj_ids)

    def test_department_updated_narrows_allocations(self):
        data = self._post_event("DEPARTMENT_UPDATED", {
            "departments": ["Engineering Dept"],
        })
        alloc_ids = self._ids(data, "allocations")
        self.assertIn(self.alloc1.pk, alloc_ids)
        self.assertNotIn(self.alloc2.pk, alloc_ids)

    # ── ALLOCATION_UPDATED ──────────────────────────────────────────
    def test_allocation_updated_narrows_resources(self):
        data = self._post_event("ALLOCATION_UPDATED", {
            "allocations": [self.alloc3.pk],
        })
        res_ids = self._ids(data, "resources")
        self.assertIn(self.resource2.pk, res_ids)
        self.assertNotIn(self.resource.pk, res_ids)

    def test_allocation_updated_does_not_narrow_statuses(self):
        """Status is an independent peer — not narrowed by allocation selection."""
        data = self._post_event("ALLOCATION_UPDATED", {
            "allocations": [self.alloc3.pk],
        })
        status_ids = self._ids(data, "statuses")
        # All statuses in the project scope remain available.
        self.assertIn("Expired", status_ids)
        self.assertIn("Active", status_ids)

    def test_allocation_updated_preserves_status_scoping(self):
        """Allocation options should still reflect status selections."""
        data = self._post_event("ALLOCATION_UPDATED", {
            "allocations": [self.alloc3.pk],
            "statuses": ["Expired"],
        })
        alloc_ids = self._ids(data, "allocations")
        # Only expired allocations in the allocation options.
        self.assertIn(self.alloc3.pk, alloc_ids)
        self.assertNotIn(self.alloc1.pk, alloc_ids)
        self.assertNotIn(self.alloc2.pk, alloc_ids)

    def test_allocation_updated_does_not_narrow_projects(self):
        """Upstream filters must not be narrowed."""
        data = self._post_event("ALLOCATION_UPDATED", {
            "allocations": [self.alloc1.pk],
        })
        proj_ids = self._ids(data, "projects")
        # Both projects should still appear.
        self.assertIn(self.proj1.pk, proj_ids)
        self.assertIn(self.proj2.pk, proj_ids)

    # ── RESOURCE_UPDATED ────────────────────────────────────────────
    def test_resource_updated_narrows_allocations(self):
        data = self._post_event("RESOURCE_UPDATED", {
            "resources": [self.resource2.pk],
        })
        alloc_ids = self._ids(data, "allocations")
        # Only alloc3 uses resource2
        self.assertIn(self.alloc3.pk, alloc_ids)
        self.assertNotIn(self.alloc1.pk, alloc_ids)
        self.assertNotIn(self.alloc2.pk, alloc_ids)

    def test_resource_updated_narrows_statuses(self):
        data = self._post_event("RESOURCE_UPDATED", {
            "resources": [self.resource2.pk],
        })
        status_ids = self._ids(data, "statuses")
        self.assertIn("Expired", status_ids)
        self.assertNotIn("Active", status_ids)

    def test_resource_updated_does_not_narrow_projects(self):
        data = self._post_event("RESOURCE_UPDATED", {
            "resources": [self.resource2.pk],
        })
        proj_ids = self._ids(data, "projects")
        self.assertIn(self.proj1.pk, proj_ids)
        self.assertIn(self.proj2.pk, proj_ids)

    # ── ALLOCATION_STATUS_UPDATED ───────────────────────────────────
    def test_status_updated_narrows_allocations(self):
        data = self._post_event("ALLOCATION_STATUS_UPDATED", {
            "statuses": ["Expired"],
        })
        alloc_ids = self._ids(data, "allocations")
        self.assertIn(self.alloc3.pk, alloc_ids)
        self.assertNotIn(self.alloc1.pk, alloc_ids)
        self.assertNotIn(self.alloc2.pk, alloc_ids)

    def test_status_updated_narrows_resources(self):
        data = self._post_event("ALLOCATION_STATUS_UPDATED", {
            "statuses": ["Expired"],
        })
        res_ids = self._ids(data, "resources")
        self.assertIn(self.resource2.pk, res_ids)
        self.assertNotIn(self.resource.pk, res_ids)

    def test_status_updated_does_not_narrow_projects(self):
        data = self._post_event("ALLOCATION_STATUS_UPDATED", {
            "statuses": ["Expired"],
        })
        proj_ids = self._ids(data, "projects")
        self.assertIn(self.proj1.pk, proj_ids)
        self.assertIn(self.proj2.pk, proj_ids)

    # ── ROLE_UPDATED ────────────────────────────────────────────────
    def test_role_updated_is_leaf(self):
        """Role is a leaf filter — nothing should be narrowed."""
        data_all = self._post_event("ROLE_UPDATED")
        data_pi  = self._post_event("ROLE_UPDATED", {
            "roles": ["Principal Investigator"],
        })
        # All other filters should have the same options regardless of role.
        for key in ("projects", "allocations", "departments", "resources", "statuses"):
            self.assertEqual(
                self._ids(data_all, key), self._ids(data_pi, key),
                f"{key} options should not change on ROLE_UPDATED",
            )

    # ── Selection pruning ───────────────────────────────────────────
    def test_stale_selection_pruned_from_selected(self):
        """Selections that are no longer valid should be dropped."""
        data = self._post_event("PROJECT_UPDATED", {
            "projects": [self.proj1.pk],
            # alloc2 belongs to proj2, not proj1 — should be pruned.
            "allocations": [self.alloc1.pk, self.alloc2.pk],
        })
        self.assertIn(self.alloc1.pk, data["allocations"]["selected"])
        self.assertNotIn(self.alloc2.pk, data["allocations"]["selected"])

    # ── Tier 1 + Tier 2 combined ────────────────────────────────────
    def test_status_respects_upstream_project_scope(self):
        """When both project and status are set, allocations should be
        scoped to the project first, then filtered by status."""
        data = self._post_event("ALLOCATION_STATUS_UPDATED", {
            "projects": [self.proj1.pk],
            "statuses": ["Active"],
        })
        alloc_ids = self._ids(data, "allocations")
        # Only alloc1 is Active in proj1.
        self.assertIn(self.alloc1.pk, alloc_ids)
        self.assertNotIn(self.alloc2.pk, alloc_ids)
        self.assertNotIn(self.alloc3.pk, alloc_ids)

    # ── Error handling ──────────────────────────────────────────────
    def test_unknown_event_returns_400(self):
        resp = self.client.post(
            reverse("notifications:filter-options"),
            json.dumps({"event": "BOGUS_EVENT", "selections": {}}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_json_returns_400(self):
        resp = self.client.post(
            reverse("notifications:filter-options"),
            "not json",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)


class ValidateViewTest(NotificationIntegrationTestCase):
    """Tests for the validate AJAX endpoint."""

    def test_validate_returns_counts(self):
        resp = self.client.post(
            reverse("notifications:validate"),
            {
                "subject": "Hi {{maint_date}}",
                "body": "Body",
                "filters": json.dumps({
                    "projects": [], "allocations": [], "resources": [],
                    "departments": [], "statuses": [], "roles": [],
                }),
                "extra_context": json.dumps({}),
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("user_count", data)
        self.assertIn("email_count", data)
        self.assertIn("scope", data)
        # Scope is elevated to at least "project" so counts match the
        # preview modal (which always shows project-level tuples).
        self.assertEqual(data["scope"], "project")


class RecipientCountViewTest(NotificationIntegrationTestCase):
    """Tests for the recipient-count endpoint."""

    def test_preview_returns_paginated_recipients(self):
        resp = self.client.post(
            reverse("notifications:recipient-count"),
            {
                "preview": "true",
                "subject": "Hi {{project_title}}",
                "body": "Body {{maint_date}}",
                "projects": [],
                "allocations": [],
                "resources": [],
                "departments": [],
                "alloc_status": [],
                "roles": [],
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("recipients", data)
        self.assertIn("count", data)
        self.assertIn("scope", data)
        self.assertIn("page", data)
        self.assertIn("total_pages", data)
        # scope should be "project" since project_title requires project
        self.assertEqual(data["scope"], "project")


class ComposeViewTest(NotificationIntegrationTestCase):
    """Tests for the compose view POST (send action)."""

    def test_compose_send_creates_campaign(self):
        # patch.object on the module directly to avoid __init__.py name shadowing
        compose_mod = sys.modules["coldfront_notifications.views.compose"]
        with patch.object(compose_mod, "_dispatch_send") as mock_dispatch:
            resp = self.client.post(reverse("notifications:compose"), {
                "action": "send",
                "subject": "Hello {{maint_date}}",
                "body": "Maintenance on {{maint_date}}.",
                "sender": "rchelp@example.com",
                "reply_to": "",
                "template_id": "",
                "filter_projects": json.dumps([]),
                "filter_allocations": json.dumps([]),
                "filter_resources": json.dumps([]),
                "filter_departments": json.dumps([]),
                "filter_statuses": json.dumps([]),
                "filter_roles": json.dumps([]),
                "extra_recipients": "",
                "dedupe_users": "[]",
            })
            # Should redirect to campaign detail
            self.assertEqual(resp.status_code, 302)
            campaign = NotificationCampaign.objects.latest("created_at")
            self.assertEqual(campaign.subject, "Hello {{maint_date}}")
            self.assertEqual(campaign.status, NotificationCampaign.STATUS_QUEUED)
            mock_dispatch.assert_called_once_with(campaign.pk)


class StaffRequiredTest(NotificationIntegrationTestCase):
    """Tests that non-staff users are blocked from all views."""

    def setUp(self):
        super().setUp()
        # Log in as user2 who is NOT staff
        self.client.force_login(self.user2)

    def test_dashboard_requires_staff(self):
        resp = self.client.get(reverse("notifications:dashboard"))
        self.assertNotEqual(resp.status_code, 200)

    def test_compose_requires_staff(self):
        resp = self.client.get(reverse("notifications:compose"))
        self.assertNotEqual(resp.status_code, 200)

    def test_campaign_list_requires_staff(self):
        resp = self.client.get(reverse("notifications:campaign-list"))
        self.assertNotEqual(resp.status_code, 200)

    def test_template_list_requires_staff(self):
        resp = self.client.get(reverse("notifications:template-list"))
        self.assertNotEqual(resp.status_code, 200)

    def test_variable_list_requires_staff(self):
        resp = self.client.get(reverse("notifications:variable-list"))
        self.assertNotEqual(resp.status_code, 200)

    def test_settings_requires_staff(self):
        resp = self.client.get(reverse("notifications:settings"))
        self.assertNotEqual(resp.status_code, 200)

    def test_anonymous_user_blocked(self):
        self.client.logout()
        resp = self.client.get(reverse("notifications:dashboard"))
        self.assertNotEqual(resp.status_code, 200)


class SeedNotificationSendersCommandTest(NotificationIntegrationTestCase):
    """Tests for the seed_notification_senders management command."""

    @override_settings(
        EMAIL_SENDER="test-sender@example.com",
        EMAIL_TICKET_SYSTEM_ADDRESS="tickets@example.com",
        EMAIL_DIRECTOR_EMAIL_ADDRESS="director@example.com",
    )
    def test_seeds_from_settings(self):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command("seed_notification_senders", stdout=out)
        output = out.getvalue()
        self.assertIn("created", output)
        self.assertTrue(SenderConfig.objects.filter(email="test-sender@example.com").exists())
        self.assertTrue(SenderConfig.objects.filter(email="tickets@example.com").exists())
        self.assertTrue(SenderConfig.objects.filter(email="director@example.com").exists())

    @override_settings(
        EMAIL_SENDER="test-sender@example.com",
        EMAIL_TICKET_SYSTEM_ADDRESS="tickets@example.com",
    )
    def test_skips_existing(self):
        from django.core.management import call_command
        from io import StringIO
        SenderConfig.objects.create(email="test-sender@example.com", label="Existing")
        out = StringIO()
        call_command("seed_notification_senders", stdout=out)
        output = out.getvalue()
        self.assertIn("already exists", output)
        # Should not duplicate
        self.assertEqual(SenderConfig.objects.filter(email="test-sender@example.com").count(), 1)

    def test_skips_unset_settings(self):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command("seed_notification_senders", stdout=out)
        self.assertIn("not set", out.getvalue())


class CampaignProgressViewTest(NotificationIntegrationTestCase):
    """Tests for the campaign progress JSON endpoint."""

    def test_progress_returns_json(self):
        campaign = NotificationCampaign.objects.create(
            subject="Test Progress",
            body="Body",
            sender="rchelp@example.com",
            status=NotificationCampaign.STATUS_SENDING,
            recipient_count=10,
            delivered_count=5,
            failed_count=1,
        )
        resp = self.client.get(
            reverse("notifications:campaign-progress", args=[campaign.pk])
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "sending")
        self.assertEqual(data["recipient_count"], 10)
        self.assertEqual(data["delivered_count"], 5)
        self.assertEqual(data["failed_count"], 1)
        self.assertEqual(data["delivery_pct"], 50.0)


# =============================================================================
# MODELS TESTS
# =============================================================================

class NotificationCampaignModelTest(NotificationIntegrationTestCase):
    """Tests for NotificationCampaign model properties."""

    def test_duration_no_times(self):
        c = NotificationCampaign(sent_at=None, completed_at=None)
        self.assertEqual(c.duration, "\u2014")

    def test_duration_seconds(self):
        now = timezone.now()
        c = NotificationCampaign(sent_at=now, completed_at=now + timedelta(seconds=45))
        self.assertEqual(c.duration, "45s")

    def test_duration_minutes(self):
        now = timezone.now()
        c = NotificationCampaign(sent_at=now, completed_at=now + timedelta(seconds=125))
        self.assertEqual(c.duration, "2m 5s")

    def test_duration_hours(self):
        now = timezone.now()
        c = NotificationCampaign(sent_at=now, completed_at=now + timedelta(seconds=3665))
        self.assertEqual(c.duration, "1h 1m 5s")

    def test_delivery_pct_zero_recipients(self):
        c = NotificationCampaign(recipient_count=0, delivered_count=0)
        self.assertEqual(c.delivery_pct, 0)

    def test_delivery_pct_normal(self):
        c = NotificationCampaign(recipient_count=10, delivered_count=7)
        self.assertEqual(c.delivery_pct, 70.0)

    def test_filter_summary(self):
        c = NotificationCampaign(filters_snapshot={
            "projects": ["Proj A"],
            "roles": ["PI", "User"],
        })
        badges = c.filter_summary
        self.assertIn("Project: Proj A", badges)
        self.assertIn("Role: PI, User", badges)

    def test_filter_summary_empty(self):
        c = NotificationCampaign(filters_snapshot={})
        self.assertEqual(c.filter_summary, [])


class NotificationTemplateModelTest(NotificationIntegrationTestCase):
    """Tests for NotificationTemplate model properties."""

    def test_variables_extracts_tokens(self):
        t = NotificationTemplate(
            subject="Hi {{user_name}}",
            body="Project: {{project_title}}, date: {{maint_date}}, again: {{user_name}}",
        )
        # Deduplicated, ordered by first occurrence
        self.assertEqual(t.variables, ["user_name", "project_title", "maint_date"])

    def test_variables_empty_body(self):
        t = NotificationTemplate(subject="No tokens here", body="Plain text.")
        self.assertEqual(t.variables, [])


class NotificationVariableModelTest(NotificationIntegrationTestCase):
    """Tests for NotificationVariable.clean() validation."""

    def test_clean_query_requires_resolver_key(self):
        v = NotificationVariable(
            key="test_q", label="Test", source="query", resolver_key="",
        )
        with self.assertRaises(ValidationError) as ctx:
            v.clean()
        self.assertIn("resolver_key", ctx.exception.message_dict)

    def test_clean_query_invalid_resolver_key(self):
        v = NotificationVariable(
            key="test_q2", label="Test", source="query", resolver_key="not.real.key",
        )
        with self.assertRaises(ValidationError) as ctx:
            v.clean()
        self.assertIn("resolver_key", ctx.exception.message_dict)

    def test_clean_manual_requires_value_when_required(self):
        v = NotificationVariable(
            key="test_m", label="Test", source="manual",
            is_required=True, value="",
        )
        with self.assertRaises(ValidationError) as ctx:
            v.clean()
        self.assertIn("value", ctx.exception.message_dict)

    def test_clean_manual_allows_empty_value_when_not_required(self):
        v = NotificationVariable(
            key="test_m2", label="Test", source="manual",
            is_required=False, value="",
        )
        # Should not raise
        v.clean()

    def test_clean_query_clears_value(self):
        v = NotificationVariable(
            key="test_q3", label="Test", source="query",
            resolver_key="user.email", value="leftover",
        )
        v.clean()
        self.assertEqual(v.value, "")


# =============================================================================
# TASKS TESTS
# =============================================================================

@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
    NOTIFICATION_BATCH_SIZE=50,
    NOTIFICATION_BATCH_DELAY=0,
    NOTIFICATION_MAX_RETRIES=0,
    NOTIFICATION_RETRY_DELAY=0,
)
class SendNotificationCampaignTaskTest(NotificationIntegrationTestCase):
    """Tests for the send_notification_campaign task."""

    def test_send_campaign_delivers_emails(self):
        from coldfront_notifications.tasks import send_notification_campaign

        campaign = NotificationCampaign.objects.create(
            subject="Hello {{maint_date}}",
            body="Maintenance on {{maint_date}}.",
            sender="rchelp@example.com",
            status=NotificationCampaign.STATUS_QUEUED,
            filters_snapshot={
                "projects": [self.proj1.pk],
                "allocations": [], "resources": [],
                "departments": [], "statuses": [], "roles": [],
            },
        )

        send_notification_campaign(campaign.pk)

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, NotificationCampaign.STATUS_SENT)
        self.assertGreater(campaign.delivered_count, 0)
        self.assertEqual(campaign.failed_count, 0)
        # Should have logs
        self.assertEqual(campaign.logs.count(), campaign.delivered_count)

    def test_send_campaign_fails_on_unknown_token(self):
        from coldfront_notifications.tasks import send_notification_campaign

        campaign = NotificationCampaign.objects.create(
            subject="Hello {{nonexistent_token}}",
            body="Body",
            sender="rchelp@example.com",
            status=NotificationCampaign.STATUS_QUEUED,
            filters_snapshot={
                "projects": [], "allocations": [], "resources": [],
                "departments": [], "statuses": [], "roles": [],
            },
        )

        send_notification_campaign(campaign.pk)

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, NotificationCampaign.STATUS_FAILED)

    def test_send_campaign_fails_on_empty_recipients(self):
        from coldfront_notifications.tasks import send_notification_campaign

        # Use an allocation status filter that matches nothing
        campaign = NotificationCampaign.objects.create(
            subject="Hello {{maint_date}}",
            body="Body with {{maint_date}}.",
            sender="rchelp@example.com",
            status=NotificationCampaign.STATUS_QUEUED,
            filters_snapshot={
                "projects": [], "allocations": [], "resources": [],
                "departments": [], "statuses": ["Expired"], "roles": [],
            },
        )

        send_notification_campaign(campaign.pk)

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, NotificationCampaign.STATUS_FAILED)
