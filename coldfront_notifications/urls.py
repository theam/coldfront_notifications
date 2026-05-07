from django.urls import path
from . import views

app_name = "notifications"

urlpatterns = [
    path("",                          views.dashboard,            name="dashboard"),
    path("compose/",                  views.compose,              name="compose"),
    path("campaigns/",                views.campaign_list,        name="campaign-list"),
    path("campaigns/<int:pk>/",       views.campaign_detail,      name="campaign-detail"),
    path("campaigns/<int:pk>/resend/",views.resend_failed,        name="resend"),
    path("campaigns/<int:pk>/resend-compose/",views.resend_compose, name="resend-compose"),
    path("campaigns/<int:pk>/progress/",views.campaign_progress, name="campaign-progress"),
    path("templates/",                views.template_list,        name="template-list"),
    path("templates/new/",            views.template_form,        name="template-create"),
    path("templates/<int:pk>/edit/",  views.template_form,        name="template-update"),
    path("templates/<int:pk>/json/",  views.template_json,        name="template-json"),
    path("templates/<int:pk>/delete/",views.template_delete,      name="template-delete"),
    path("variables/",                views.variable_list,        name="variable-list"),
    path("variables/new/",            views.variable_form,        name="variable-create"),
    path("variables/<int:pk>/edit/",  views.variable_form,        name="variable-update"),
    path("variables/<int:pk>/delete/",views.variable_delete,      name="variable-delete"),
    path("settings/",                 views.settings_view,        name="settings"),
    path("settings/senders/new/",     views.sender_form,          name="sender-create"),
    path("settings/senders/<int:pk>/edit/", views.sender_form,    name="sender-update"),
    path("settings/senders/<int:pk>/delete/", views.sender_delete,name="sender-delete"),
    path("recipient-count/",          views.recipient_count_view, name="recipient-count"),
    path("filter-options/",           views.filter_options,       name="filter-options"),
    path("variables.json",            views.variables_view,       name="variables"),
    path("validate/",                 views.validate_view,        name="validate"),
    path("preview-render/",           views.preview_render_view,  name="preview-render"),
]
