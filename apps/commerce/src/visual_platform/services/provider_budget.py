"""Register reviewed mock-protocol ceilings; never execute a provider or move credits."""

import hashlib
from uuid import UUID

import psycopg

from visual_platform.db.result import required_row
from visual_platform.providers.authorization import (
    DualBailianSmokeAuthorization,
    ProviderAuthorization,
    canonical_authorization,
)
from visual_platform.services.rule_operations import assert_operator


def register_provider_budget(
    dsn: str,
    quote: UUID,
    scope: ProviderAuthorization,
    reviewed_sha256: str,
    operation: UUID,
    reason: str,
) -> str:
    if isinstance(scope, DualBailianSmokeAuthorization):
        raise ValueError("dual_bailian_budget_registration_not_enabled")
    canonical = canonical_authorization(scope)
    digest = hashlib.sha256(canonical).hexdigest()
    if digest != reviewed_sha256:
        raise ValueError("reviewed scope hash differs")
    with psycopg.connect(dsn) as connection:
        assert_operator(connection)
        identity = required_row(
            connection.execute(
                "SELECT ops.register_provider_budget(%s,%s,%s,%s,%s,%s)",
                (scope.authorization_id, quote, canonical.decode(), digest, operation, reason),
            )
        )[0]
    return str(identity)


def provider_budget_snapshot(dsn: str, identity: UUID):
    with psycopg.connect(dsn) as connection:
        assert_operator(connection)
        result = required_row(
            connection.execute("SELECT ops.provider_budget_snapshot(%s)", (identity,))
        )[0]
    if result is None:
        raise ValueError("provider budget not found")
    return result


def provider_quote_review(dsn: str, user: UUID, quote: UUID):
    with psycopg.connect(dsn) as connection:
        assert_operator(connection)
        return required_row(
            connection.execute("SELECT ops.provider_quote_review(%s,%s)", (user, quote))
        )[0]


def qwen_generation_projection(dsn: str, user: UUID, quote: UUID):
    """Read a scoped pre-submission prediction; never create observations or spending authority."""
    with psycopg.connect(dsn) as connection:
        assert_operator(connection)
        return required_row(
            connection.execute("SELECT ops.qwen_generation_projection(%s,%s)", (user, quote))
        )[0]


def review_blocked_provider_budget(
    dsn: str,
    identity: UUID,
    receipt: UUID,
    scope_sha: str,
    operation: UUID,
    reason: str,
    evidence: str,
):
    with psycopg.connect(dsn) as connection:
        assert_operator(connection)
        return str(
            required_row(
                connection.execute(
                    "SELECT ops.review_blocked_provider_budget(%s,%s,%s,%s,%s,%s)",
                    (identity, receipt, scope_sha, operation, reason, evidence),
                )
            )[0]
        )
