"""
Integration tests for the coldfront_notifications plugin.

Requires Django's test database with ColdFront models available.
"""
import json
from datetime import timedelta
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
    """Tests for the filter-options AJAX endpoint."""

    def test_filter_options_with_project(self):
        resp = self.client.post(
            reverse("notifications:filter-options"),
            {"projects": [self.proj1.pk]},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("allocations", data)
        # Should include alloc1 (proj1) but not alloc2 (proj2)
        alloc_ids = [a["id"] for a in data["allocations"]]
        self.assertIn(self.alloc1.pk, alloc_ids)
        self.assertNotIn(self.alloc2.pk, alloc_ids)


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
        self.assertEqual(data["scope"], "user")


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

    @patch("coldfront_notifications.views._dispatch_send")
    def test_compose_send_creates_campaign(self, mock_dispatch):
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
