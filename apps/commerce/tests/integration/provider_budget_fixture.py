"""Shared real non-owner operator and frozen mock-quote budget fixture, no tests."""

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from test_protocol_pipeline import ProtocolPipelineFixture

from visual_platform.providers.authorization import (
    SmokeAuthorization,
    UnitSmokeAuthorization,
    canonical_authorization,
)
from visual_platform.services.provider_budget import (
    provider_budget_snapshot,
    register_provider_budget,
)
from visual_platform.services.quotes import hash_data


class ProviderBudgetFixture(ProtocolPipelineFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.operator = "budget_" + uuid4().hex
        password = secrets.token_urlsafe(32)
        with psycopg.connect(cls.owner) as c:
            c.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
                ).format(sql.Identifier(cls.operator), sql.Literal(password))
            )
            c.execute(sql.SQL("GRANT ops_admin TO {}").format(sql.Identifier(cls.operator)))
        cls.operator_dsn = make_conninfo(cls.owner, user=cls.operator, password=password)

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(cls.owner) as c:
            c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(cls.operator)))
        super().tearDownClass()

    def bind(self, quote, snapshot, *, lifetime=3600, changed=None):
        now = datetime.now(timezone.utc)
        data = {
            "authorization_id": str(uuid4()),
            "user_id": str(self.user),
            "product_id": self.product,
            "source_asset_version_id": self.version,
            "authorized_by": "synthetic offline review, not spending permission",
            "authorized_at": (now - timedelta(seconds=1)).isoformat(),
            "expires_at": (now + timedelta(seconds=lifetime)).isoformat(),
            "source_path": "synthetic.png",
            "source_sha256": snapshot["_execution"]["source_sha256"],
            "quote_input_sha256": hash_data(snapshot),
            "generation": snapshot["generation"],
            "targets": snapshot["targets"],
            "generate_ceiling": "0.10",
            "detect_ceiling": "0.02",
            "total_ceiling": "0.12",
        }
        if changed:
            data.update(changed)
        model = SmokeAuthorization
        if data.get("schema_version") == 3:
            model = UnitSmokeAuthorization
            for key in ("generate_ceiling", "detect_ceiling", "total_ceiling"):
                data.pop(key)
        self.scope = model.model_validate_json(json.dumps(data))
        self.digest = hashlib.sha256(canonical_authorization(self.scope)).hexdigest()
        self.operation = uuid4()
        self.budget = register_provider_budget(
            self.operator_dsn,
            quote,
            self.scope,
            self.digest,
            self.operation,
            "synthetic offline budget",
        )

    def snapshot(self):
        return provider_budget_snapshot(self.operator_dsn, UUID(self.budget))
