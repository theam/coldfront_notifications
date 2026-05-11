"""
Pre-flight validation for NotificationCampaign.

validate_campaign(subject, body, filters, extra_context) runs every
{{token}} against every recipient tuple and returns:

    {
      "errors":         [{"email":..., "token":..., "reason":...}, ...],
      "user_count":     N,     # distinct users that would receive >= 1 email
      "email_count":    M,     # total emails to send (M >= N under scope=allocation)
      "multi_emails":   [{"username":..., "count":...}, ...],
      "missing_tokens": [...]  # tokens in subject/body with no matching NotificationVariable
      "scope":          "user" | "project" | "allocation",
    }

Campaign is sendable iff errors == [] and missing_tokens == [].
"""
import re
from collections import Counter

from .resolvers import QUERY_SCOPES, MissingValue, resolve
from .utils import enumerate_recipients_deduped

TOKEN_RE = re.compile(r"\{\{(\w+)\}\}")


def _extract_tokens(subject: str, body: str) -> list:
    """Return unique list of tokens in order of first occurrence."""
    seen = set()
    out = []
    for s in (subject, body):
        for m in TOKEN_RE.finditer(s or ""):
            k = m.group(1)
            if k not in seen:
                seen.add(k)
                out.append(k)
    return out


_SCOPE_RANK = {None: -1, "user": 0, "project": 1, "allocation": 2}


def _required_scope(variables) -> str:
    """Widest scope across used query variables (manuals don't affect scope)."""
    rank = 0  # user
    for v in variables:
        if v.source != v.SOURCE_QUERY:
            continue
        r = _SCOPE_RANK.get(QUERY_SCOPES.get(v.resolver_key), 0)
        if r > rank:
            rank = r
    return {0: "user", 1: "project", 2: "allocation"}[rank]


def validate_campaign(subject: str, body: str, filters: dict, extra_context: dict,
                      dedupe_users=None) -> dict:
    from .models import NotificationVariable

    tokens = _extract_tokens(subject, body)
    vars_by_key = {v.key: v for v in NotificationVariable.objects.filter(key__in=tokens)}

    missing_tokens = [t for t in tokens if t not in vars_by_key]
    used_vars = [vars_by_key[t] for t in tokens if t in vars_by_key]
    scope = _required_scope(used_vars)

    errors = []
    user_pks = set()
    user_email_counter = Counter()

    for (user, project, allocation) in enumerate_recipients_deduped(filters, scope, dedupe_users):
        user_pks.add(user.pk)
        user_email_counter[user.username] += 1
        ctx = {"user": user, "project": project, "allocation": allocation}
        for token in tokens:
            v = vars_by_key.get(token)
            if v is None:
                continue  # already reported in missing_tokens
            try:
                if v.source == NotificationVariable.SOURCE_MANUAL:
                    if v.is_required and not (v.value or "").strip():
                        raise MissingValue()
                else:
                    resolve(v.resolver_key, ctx)
            except MissingValue:
                errors.append({
                    "email":  user.email,
                    "token":  token,
                    "reason": (
                        f"manual variable {v.key!r} has no stored value"
                        if v.source == NotificationVariable.SOURCE_MANUAL
                        else f"cannot resolve {v.resolver_key} for this recipient"
                    ),
                })

    multi_emails = sorted(
        [{"username": u, "count": c} for u, c in user_email_counter.items() if c > 1],
        key=lambda x: -x["count"],
    )

    return {
        "errors":         errors,
        "user_count":     len(user_pks),
        "email_count":    sum(user_email_counter.values()),
        "multi_emails":   multi_emails,
        "missing_tokens": missing_tokens,
        "scope":          scope,
    }
