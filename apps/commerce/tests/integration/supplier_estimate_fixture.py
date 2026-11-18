"""Two-step synthetic metering fixture, no test cases or live transport."""

from supplier_pricing_fixture import SupplierPricingFixture


class SupplierEstimateFixture(SupplierPricingFixture):
    def metered(
        self,
        *,
        generate_rate="0.05",
        currency="USD",
        detect=False,
        complete=True,
        reported_model="gemini-3.8-flash",
        detect_currency="USD",
    ):
        price, identity = self.publish(
            self.document(
                currency=currency,
                rates=[{"measure": "bfl_credit", "amount": generate_rate, "per": 1}],
            )
        )
        execution = self.execution(price, identity)
        if detect:
            vision, vision_id = self.publish(
                self.document(
                    provider="gemini",
                    currency=detect_currency,
                    model="gemini-3.8-flash",
                    endpoint_origin="https://generativelanguage.googleapis.com",
                    rates=[
                        {"measure": "input_tokens", "amount": "0.50", "per": 1000000},
                        {"measure": "cached_input_tokens", "amount": "0.10", "per": 1000000},
                        {"measure": "output_tokens", "amount": "1", "per": 1000000},
                        {"measure": "thinking_tokens", "amount": "1", "per": 1000000},
                    ],
                )
            )
            execution["provider_accounts"]["detect"] = {
                "merchant_fingerprint": vision.merchant_fingerprint,
                "billing_group": vision.billing_group,
            }
            import hashlib

            from visual_platform.domain.provider_pricing import canonical_price

            execution["provider_price_versions"]["detect"] = {
                "id": vision_id,
                "document_sha256": hashlib.sha256(canonical_price(vision)).hexdigest(),
            }
            route = self.gemini_route

            def gemini(request):
                response = route(request)
                data = response.json()
                data["modelVersion"] = reported_model
                if complete:
                    data["usageMetadata"] = {
                        "promptTokenCount": 10,
                        "cachedContentTokenCount": 2,
                        "candidatesTokenCount": 20,
                        "thoughtsTokenCount": 3,
                        "toolUsePromptTokenCount": 0,
                        "totalTokenCount": 33,
                    }
                import httpx

                return httpx.Response(200, json=data)

            self.gemini_route = gemini
        task = self.submit(self.bind, execution_updates=execution)
        return task, execution["provider_accounts"]
