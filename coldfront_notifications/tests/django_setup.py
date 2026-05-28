"""
Django configuration for unit tests.

When ColdFront is installed (Docker, local dev), uses the full app stack.
When running standalone (CI), stubs the ColdFront modules so that
top-level imports in plugin code resolve without errors.

Import this module before any Django/plugin imports in test files.
"""
import sys
import types

import django
from django.conf import settings


def _stub_coldfront_modules():
    """Register stub modules for ColdFront packages not installed in CI.

    This lets top-level ``from coldfront.core.project.models import ...``
    resolve to MagicMock objects instead of raising ImportError.
    """
    from unittest.mock import MagicMock

    stub_paths = [
        "coldfront",
        "coldfront.core",
        "coldfront.core.allocation",
        "coldfront.core.allocation.models",
        "coldfront.core.department",
        "coldfront.core.department.models",
        "coldfront.core.field_of_science",
        "coldfront.core.field_of_science.models",
        "coldfront.core.project",
        "coldfront.core.project.models",
        "coldfront.core.resource",
        "coldfront.core.resource.models",
        "coldfront.core.user",
        "coldfront.core.utils",
        "coldfront.core.portal",
        "coldfront.core.grant",
        "coldfront.core.publication",
        "coldfront.core.research_output",
        "coldfront.plugins",
        "coldfront.plugins.ifx",
        "coldfront.plugins.ifx.models",
        "ifxuser",
        "ifxuser.models",
        "ifxbilling",
        "ifxbilling.models",
    ]
    for path in stub_paths:
        if path not in sys.modules:
            sys.modules[path] = MagicMock()


def _has_coldfront():
    """Check if ColdFront is actually installed."""
    try:
        import coldfront  # noqa: F401
        return True
    except ImportError:
        return False


if not settings.configured:
    if _has_coldfront():
        settings.configure(
            INSTALLED_APPS=[
                "django.contrib.contenttypes",
                "django.contrib.auth",
                "django.contrib.admin",
                "django.contrib.sessions",
                "django.contrib.messages",
                "coldfront.core.field_of_science",
                "coldfront.core.project",
                "coldfront.core.resource",
                "coldfront.core.allocation",
                "coldfront.core.department",
                "coldfront.core.user",
                "coldfront.core.utils",
                "coldfront.plugins.ifx",
                "ifxuser",
                "ifxbilling",
                "simple_history",
                "django_q",
                "coldfront_notifications",
            ],
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": ":memory:",
                }
            },
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
            AUTH_USER_MODEL="ifxuser.IfxUser",
            SECRET_KEY="test-secret-key-not-for-production",
            IFX_APP={"token": "test-token"},
        )
    else:
        _stub_coldfront_modules()
        settings.configure(
            INSTALLED_APPS=[
                "django.contrib.contenttypes",
                "django.contrib.auth",
                "coldfront_notifications",
            ],
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": ":memory:",
                }
            },
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
            SECRET_KEY="test-secret-key-not-for-production",
        )

    django.setup()
