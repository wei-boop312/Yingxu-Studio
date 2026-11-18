"""Shared archived-price fixture without test methods."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from provider_budget_fixture import ProviderBudgetFixture

from visual_platform.domain.provider_pricing import SupplierPriceVersion, canonical_price
from visual_platform.providers.pipeline import BASELINE_ENDPOINTS
from visual_platform.services.provider_pricing import publish_supplier_price
from visual_platform.storage.s3 import S3Storage


class SupplierPricingFixture(ProviderBudgetFixture):
    evidence = b"Synthetic offline rate evidence; not actual supplier prices or invoice."

    def document(self, **changes):
        now = datetime.now(timezone.utc)
        data = {
            "schema_version": 1,
            "provider": "bfl",
            "model": "flux-3-image",
            "endpoint_origin": "https://api.bfl.ai",
            "merchant_fingerprint": "a" * 64,
            "billing_group": "synthetic-only",
            "version": "synthetic-" + uuid4().hex,
            "currency": "USD",
            "valid_from": (now - timedelta(minutes=1)).isoformat(),
            "valid_until": (now + timedelta(hours=1)).isoformat(),
            "evidence_kind": "offline_fixture",
            "evidence_sha256": hashlib.sha256(self.evidence).hexdigest(),
            "complete_meter_coverage": True,
            "rates": [{"measure": "bfl_credit", "amount": "0.05", "per": 1}],
            **changes,
        }
        return SupplierPriceVersion.model_validate_json(json.dumps(data))

    def publish(self, price=None, operation=None):
        price = price or self.document()
        storage = S3Storage(self.settings)
        try:
            identity = publish_supplier_price(
                self.operator_dsn,
                storage,
                price,
                self.evidence,
                operation or uuid4(),
                "synthetic archived price review",
                evidence_reviewed=True,
            )
            return price, identity
        finally:
            storage.close()

    def execution(self, price, identity):
        accounts = {
            "generate": {
                "merchant_fingerprint": price.merchant_fingerprint,
                "billing_group": price.billing_group,
            }
        }
        return {
            "provider_accounts": accounts,
            "provider_endpoints": BASELINE_ENDPOINTS,
            "provider_price_versions": {
                "generate": {
                    "id": identity,
                    "document_sha256": hashlib.sha256(canonical_price(price)).hexdigest(),
                }
            },
        }
