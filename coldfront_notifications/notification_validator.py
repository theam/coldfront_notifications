"""
Pre-flight validation for notification campaigns.

NotificationValidator checks that every {{token}} in the subject and body
can be resolved for every recipient. It produces a validation result with
errors, counts, and scope information.

A campaign is sendable when errors == [] and missing_tokens == [].
"""
from __future__ import annotations

import re
from collections import Counter

from .filters import RecipientResolver
from .models import NotificationVariable
from .template_variable_value_resolver import MissingValue, registry as resolver_registry

TOKEN_PATTERN = re.compile(r"\{\{(\w+)\}\}")

SCOPE_RANK = {"user": 0, "project": 1, "allocation": 2}
SCOPE_BY_RANK = {0: "user", 1: "project", 2: "allocation"}


class NotificationValidator:
    """Validates a notification campaign before sending.

    Extracts tokens from subject/body, resolves each against every
    recipient tuple, and collects errors for unresolvable variables.
    """

    def __init__(self, subject: str, body: str, filters: dict,
                 dedupe_users=None, scope_override=None):
        self.subject = subject
        self.body = body
        self.filters = filters
        self.dedupe_users = dedupe_users
        self.scope_override = scope_override

    def validate(self) -> dict:
        """Run validation and return the result dict."""
        tokens = self._extract_tokens()
        variables_by_key = self._load_variables(tokens)
        missing_tokens = [
            token for token in tokens if token not in variables_by_key
        ]
        matched_variables = [
            variables_by_key[token] for token in tokens if token in variables_by_key
        ]
        scope = self.scope_override or self._determine_scope(matched_variables)

        resolution_errors = []
        unique_user_pks = set()
        emails_per_user = Counter()

        resolver = RecipientResolver(self.filters)
        for user, project, allocation in resolver.enumerate_deduped(scope, self.dedupe_users):
            unique_user_pks.add(user.pk)
            emails_per_user[user.username] += 1
            context = {"user": user, "project": project, "allocation": allocation}

            for token in tokens:
                variable = variables_by_key.get(token)
                if variable is None:
                    continue

                error = self._validate_variable(variable, context)
                if error:
                    resolution_errors.append({
                        "email": user.email,
                        "username": user.username,
                        "full_name": user.get_full_name() or user.username,
                        "token": token,
                        "reason": error,
                        "project": project.title if project else "",
                        "project_pk": project.pk if project else None,
                        "allocation_pk": allocation.pk if allocation else None,
                        "allocation_label": (
                            f"{allocation.pk} — {allocation.get_parent_resource}"
                            if allocation else ""
                        ),
                    })

        multi_email_users = sorted(
            [
                {"username": username, "count": count}
                for username, count in emails_per_user.items()
                if count > 1
            ],
            key=lambda entry: -entry["count"],
        )

        return {
            "errors": resolution_errors,
            "user_count": len(unique_user_pks),
            "email_count": sum(emails_per_user.values()),
            "multi_emails": multi_email_users,
            "missing_tokens": missing_tokens,
            "scope": scope,
        }

    def _extract_tokens(self) -> list[str]:
        """Return unique tokens from subject + body in order of first occurrence."""
        seen = set()
        tokens = []
        for text in (self.subject, self.body):
            for match in TOKEN_PATTERN.finditer(text or ""):
                token = match.group(1)
                if token not in seen:
                    seen.add(token)
                    tokens.append(token)
        return tokens

    def _load_variables(self, tokens: list[str]) -> dict:
        """Load NotificationVariable objects for the given tokens."""
        return {
            variable.key: variable
            for variable in NotificationVariable.objects.filter(key__in=tokens)
        }

    def _determine_scope(self, variables) -> str:
        """Return the widest scope required by the given variables."""
        highest_rank = 0
        for variable in variables:
            if variable.source != NotificationVariable.Source.QUERY:
                continue
            variable_scope = resolver_registry.scopes.get(variable.resolver_key)
            rank = SCOPE_RANK.get(variable_scope, 0)
            if rank > highest_rank:
                highest_rank = rank
        return SCOPE_BY_RANK[highest_rank]

    def _validate_variable(self, variable, context: dict) -> str | None:
        """Validate a single variable against a recipient context.

        Returns an error message string, or None if valid.
        """
        try:
            if variable.source == NotificationVariable.Source.MANUAL:
                if variable.is_required and not (variable.value or "").strip():
                    raise MissingValue()
            else:
                resolver_registry.resolve(variable.resolver_key, context)
            return None
        except MissingValue:
            if variable.source == NotificationVariable.Source.MANUAL:
                return f"manual variable {variable.key!r} has no stored value"
            return f"cannot resolve {variable.resolver_key} for this recipient"



def extract_tokens(subject: str, body: str) -> list[str]:
    """Extract unique {{token}} names from subject and body."""
    seen = set()
    tokens = []
    for text in (subject, body):
        for match in TOKEN_PATTERN.finditer(text or ""):
            token = match.group(1)
            if token not in seen:
                seen.add(token)
                tokens.append(token)
    return tokens


def determine_scope(variables) -> str:
    """Return the widest scope required by the given variables."""
    highest_rank = 0
    for variable in variables:
        if variable.source != NotificationVariable.Source.QUERY:
            continue
        variable_scope = resolver_registry.scopes.get(variable.resolver_key)
        rank = SCOPE_RANK.get(variable_scope, 0)
        if rank > highest_rank:
            highest_rank = rank
    return SCOPE_BY_RANK[highest_rank]
