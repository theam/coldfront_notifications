from django.urls import path
from . import views

app_name = "notifications"

urlpatterns = [
    # Dashboard
    path(
        "",
        views.DashboardView.as_view(),
        name="dashboard",
    ),

    # Compose
    path(
        "compose/",
        views.ComposeView.as_view(),
        name="compose",
    ),

    # Campaigns
    path(
        "campaigns/",
        views.CampaignListView.as_view(),
        name="campaign-list",
    ),
    path(
        "campaigns/<int:pk>/",
        views.CampaignDetailView.as_view(),
        name="campaign-detail",
    ),
    path(
        "campaigns/<int:pk>/resend/",
        views.ResendFailedView.as_view(),
        name="resend",
    ),
    path(
        "campaigns/<int:pk>/resend-compose/",
        views.ResendComposeView.as_view(),
        name="resend-compose",
    ),
    path(
        "campaigns/<int:pk>/progress/",
        views.CampaignProgressView.as_view(),
        name="campaign-progress",
    ),

    # Templates
    path(
        "templates/",
        views.TemplateListView.as_view(),
        name="template-list",
    ),
    path(
        "templates/new/",
        views.TemplateFormView.as_view(),
        name="template-create",
    ),
    path(
        "templates/<int:pk>/edit/",
        views.TemplateFormView.as_view(),
        name="template-update",
    ),
    path(
        "templates/<int:pk>/json/",
        views.TemplateJsonView.as_view(),
        name="template-json",
    ),
    path(
        "templates/<int:pk>/delete/",
        views.TemplateDeleteView.as_view(),
        name="template-delete",
    ),

    # Variables
    path(
        "variables/",
        views.VariableListView.as_view(),
        name="variable-list",
    ),
    path(
        "variables/new/",
        views.VariableFormView.as_view(),
        name="variable-create",
    ),
    path(
        "variables/<int:pk>/edit/",
        views.VariableFormView.as_view(),
        name="variable-update",
    ),
    path(
        "variables/<int:pk>/delete/",
        views.VariableDeleteView.as_view(),
        name="variable-delete",
    ),

    # Settings & senders
    path(
        "settings/",
        views.SettingsView.as_view(),
        name="settings",
    ),
    path(
        "settings/senders/new/",
        views.SenderFormView.as_view(),
        name="sender-create",
    ),
    path(
        "settings/senders/<int:pk>/edit/",
        views.SenderFormView.as_view(),
        name="sender-update",
    ),
    path(
        "settings/senders/<int:pk>/delete/",
        views.SenderDeleteView.as_view(),
        name="sender-delete",
    ),

    # API endpoints
    path(
        "api/recipient-count/",
        views.RecipientCountView.as_view(),
        name="api-recipient-count",
    ),
    path(
        "api/variables/",
        views.VariablesJsonView.as_view(),
        name="api-variables",
    ),
    path(
        "api/validate/",
        views.ValidateView.as_view(),
        name="api-validate",
    ),
    path(
        "api/preview-render/",
        views.PreviewRenderView.as_view(),
        name="api-preview-render",
    ),
    path(
        "api/draft/save/",
        views.DraftSaveView.as_view(),
        name="api-draft-save",
    ),
]
