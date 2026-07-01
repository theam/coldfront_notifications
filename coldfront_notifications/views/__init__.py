# Re-export all views so urls.py can use `from . import views`.
from .dashboard import DashboardView  # noqa: F401
from .campaigns import (  # noqa: F401
    CampaignListView, CampaignDetailView, CampaignProgressView,
    DraftDeleteView, ResendFailedView, ResendComposeView,
)
from .compose import (  # noqa: F401
    ComposeView, RecipientCountView,
    PreviewRenderView, ValidateView,
    DraftSaveView,
)
from .helpers import dispatch_send  # noqa: F401
from .templates import (  # noqa: F401
    TemplateListView, TemplateFormView,
    TemplateDeleteView, TemplateJsonView,
)
from .settings import (  # noqa: F401
    VariableListView, VariableFormView,
    VariableDeleteView, VariablesJsonView,
    SettingsView, SenderFormView, SenderDeleteView,
)
