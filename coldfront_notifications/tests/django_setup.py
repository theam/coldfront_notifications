"""
Django configuration for unit tests. Bootstraps the full ColdFront app
stack so that top-level model imports in plugin modules resolve correctly.

Import this module before any Django/plugin imports in test files.
"""
import os
import sys

# Add coldfront root and sibling packages to sys.path
_coldfront_root = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
_sibling_packages = (
    "",
    "ifxuser",
    "ifxbilling",
    "ifxreport",
    "ifxec",
    "ifxmail.client",
    "ifxurls",
    "nanites.client",
    "fiine.client",
)
for _subdir in _sibling_packages:
    _path = os.path.join(_coldfront_root, _subdir)
    if _path not in sys.path:
        sys.path.insert(0, _path)

import django
from django.conf import settings

if not settings.configured:
    # Replicate the INSTALLED_APPS from coldfront.config.base
    # Hack required by ColdFront for fontawesome
    sys.modules['fontawesome_free'] = __import__('fontawesome-free')

    settings.configure(
        INSTALLED_APPS=[
            "django.contrib.admin",
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "django.contrib.sessions",
            "django.contrib.messages",
            "django.contrib.staticfiles",
            "django.contrib.humanize",
            "crispy_forms",
            "crispy_bootstrap4",
            "simple_history",
            "fontawesome_free",
            "django_q",
            "coldfront.core.user",
            "coldfront.core.field_of_science",
            "coldfront.core.utils",
            "coldfront.core.portal",
            "coldfront.core.project",
            "coldfront.core.resource",
            "coldfront.core.allocation",
            "coldfront.core.grant",
            "coldfront.core.department",
            "coldfront.core.publication",
            "coldfront.core.research_output",
            "coldfront.plugins.ifx",
            "ifxuser",
            "ifxbilling",
            "ifxreport",
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
        CRISPY_TEMPLATE_PACK="bootstrap4",
        SECRET_KEY="test-secret-key-not-for-production",
        IFX_APP={"token": "test-token"},
    )
    django.setup()
