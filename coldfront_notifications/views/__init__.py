# Re-export all views so urls.py can continue using `from . import views`.
from .dashboard import dashboard                                          # noqa: F401
from .campaigns import (                                                  # noqa: F401
    campaign_list, campaign_detail, campaign_progress,
    resend_failed, resend_compose,
)
from .compose import (                                                    # noqa: F401
    compose, filter_options, recipient_count_view,
    preview_render_view, validate_view, _dispatch_send,
)
from .templates import (                                                  # noqa: F401
    template_list, template_form, template_delete, template_json,
)
from .settings import (                                                   # noqa: F401
    variable_list, variable_form, variable_delete, variables_view,
    settings_view, sender_form, sender_delete,
)
