"""Actual PG/S3, tenant and quote bindings; supplier HTTP is synthetic only."""

import json
import os
import tempfile
from dataclasses import replace
from unittest.mock import patch
from uuid import uuid4

import httpx
import psycopg
import test_live_bailian as live
from bailian_fixture import BailianFixture

from visual_platform.core.identity import hash_password
from visual_platform.providers.appearance_understanding import understand
from visual_platform.providers.runtime import ProviderRuntime
from visual_platform.services.runtime_quote import register_runtime_configuration


class AppearanceUnderstandingTests(BailianFixture):
    def setUp(self):
        super().setUp()
        document = self.qwen_runtime.model_dump()
        document["schema_version"] = 3
        document["version"] += "-live"
        document["detect"] = {
            **document["generate"],
            "provider": "aliyun_bailian_vision",
            "model": "qwen3.8-flash",
        }
        self.live_runtime = ProviderRuntime.model_validate(document, strict=True)
        register_runtime_configuration(
            self.operator_dsn,
            self.live_runtime,
            self.live_runtime.digest(),
            uuid4(),
            "synthetic live routing identity",
        )
        self.live_rates = {
            "generate": {
                "qima_input_1k": "0.02",
                "qima_input_2k": "0.02",
                "qima_output_1k": "0.18",
                "qima_output_2k": "0.18",
            },
            "detect": {"input_token": "0.8", "output_token": "2.7", "input_token_cache": "0.1"},
        }
        self.client.app.state.settings = replace(self.settings, provider_profile="bailian_live")
        self.empty_detection = False
        live.LiveBailianTests.policy(self)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.calls = []
        self.timeout = False
        self.raw = {
            "model": "qwen3.8-flash",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "description": "红色方形商品，正面无可辨认标识。",
                                "colors": ["红色"],
                                "materials": [],
                                "uncertainties": ["材质无法确定"],
                            }
                        ),
                    },
                }
            ],
        }

        def route(request):
            self.calls.append(request)
            if self.timeout:
                raise httpx.ReadTimeout("synthetic response loss")
            return httpx.Response(200, json=self.raw)

        async def adapter(*args, **kwargs):
            kwargs["live"] = False  # Tests replace explicit live transport before any network.
            return await understand(*args, **kwargs, transport=httpx.MockTransport(route))

        for patcher in (
            patch("visual_platform.api.appearance_understanding.understand", side_effect=adapter),
            patch(
                "visual_platform.api.appearance_understanding.load_runtime",
                return_value=self.live_runtime,
            ),
            patch(
                "visual_platform.api.appearance_understanding.private_credential",
                return_value="offline-bailian",
            ),
            patch.dict(
                os.environ,
                {
                    "LIVE_PROVIDER_RUNTIME_FILE": "synthetic",
                    "LIVE_PROVIDER_PRIVATE_DIR": self.directory.name,
                },
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def quote(self):
        result = self.client.post(
            "/api/v1/appearance-understandings/quotes",
            json={"product_id": self.product, "source_asset_version_id": self.version},
            headers=self.write_headers(),
        )
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()

    def submit_understanding(self, identity):
        return self.client.post(
            "/api/v1/appearance-understandings/" + identity + "/submit",
            headers={**self.write_headers(), "X-Provider-Confirmation": "bailian_live"},
        )

    def test_confirmed_edit_bound_to_original_is_frozen_in_generation_quote(self):
        quote = self.quote()
        identity = quote["id"]
        self.assertIsNone(quote["actual_cost_cny"])
        self.assertEqual(
            self.client.post(
                "/api/v1/appearance-understandings/" + identity + "/submit",
                headers=self.write_headers(),
            ).status_code,
            409,
        )
        result = self.submit_understanding(identity)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["status"], "succeeded")
        self.assertEqual(self.submit_understanding(identity).json()["status"], "succeeded")
        self.assertEqual(len(self.calls), 1)
        with psycopg.connect(self.owner) as c:
            c.execute(
                "UPDATE app.appearance_understandings SET status='uncertain',result=NULL WHERE id=%s",
                (identity,),
            )
        recovered = self.client.post(
            "/api/v1/appearance-understandings/" + identity + "/recover",
            headers=self.write_headers(),
        )
        self.assertEqual(recovered.json()["status"], "succeeded")
        self.assertEqual(len(self.calls), 1)
        body = {
            "name": "已校订的外观",
            "confirmed": True,
            "config": {
                "description": "用户校订：红色方形产品，保留原有轮廓。",
                "colors": ["红色"],
                "materials": [],
            },
        }
        endpoint = "/api/v1/appearance-understandings/" + identity + "/confirm"
        self.assertEqual(
            self.client.post(
                endpoint, json={**body, "confirmed": False}, headers=self.write_headers()
            ).status_code,
            422,
        )
        response = self.client.post(endpoint, json=body, headers=self.write_headers())
        self.assertEqual(response.status_code, 201, response.text)
        profile = response.json()
        self.assertEqual(profile["config"]["source_asset_version_id"], self.version)
        self.assertEqual(profile["config"]["understanding_id"], identity)
        self.assertEqual(
            self.client.post(endpoint, json=body, headers=self.write_headers()).json()["id"],
            profile["id"],
        )
        _, generation = self.source_quote()
        generation["appearance_profile_id"] = profile["id"]
        quoted = self.client.post("/api/v1/quotes", json=generation, headers=self.write_headers())
        self.assertEqual(quoted.status_code, 201, quoted.text)
        with psycopg.connect(self.owner) as c:
            snapshot = c.execute(
                "SELECT input_snapshot FROM app.quotes WHERE id=%s", (quoted.json()["quote_id"],)
            ).fetchone()[0]
        self.assertEqual(
            snapshot["_execution"]["appearance"]["config"]["description"],
            body["config"]["description"],
        )
        self.assertEqual(snapshot["generation"]["protection_mode"], "none")
        other = self.complete(self.upload(self.source)).json()["version_id"]
        mismatch = self.client.post(
            "/api/v1/quotes",
            json={**generation, "source_asset_version_id": other},
            headers=self.write_headers(),
        )
        self.assertEqual(mismatch.status_code, 409, mismatch.text)

    def test_unknown_is_durable_blocks_second_quote_and_operator_closure_preserves_cost_unknown(
        self,
    ):
        identity = self.quote()["id"]
        self.timeout = True
        self.assertEqual(self.submit_understanding(identity).json()["status"], "uncertain")
        self.assertEqual(self.submit_understanding(identity).json()["status"], "uncertain")
        self.assertEqual(len(self.calls), 1)
        blocked = self.client.post(
            "/api/v1/appearance-understandings/quotes",
            json={"product_id": self.product, "source_asset_version_id": self.version},
            headers=self.write_headers(),
        )
        self.assertEqual(blocked.status_code, 409)
        with psycopg.connect(self.operator_dsn) as c:
            c.execute(
                "SELECT ops.close_appearance_understanding(%s,%s,%s,%s,%s)",
                (
                    self.user,
                    identity,
                    uuid4(),
                    "Synthetic delivery closure, no supplier refund asserted",
                    "a" * 64,
                ),
            )
        result = self.client.get("/api/v1/appearance-understandings/" + identity).json()
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["actual_cost_cny"])

    def test_cross_tenant_source_and_record_are_invisible_and_revocation_stops_post(self):
        identity = self.quote()["id"]
        live.LiveBailianTests.policy(self, False)
        self.assertEqual(self.submit_understanding(identity).status_code, 409)
        self.assertEqual(self.calls, [])
        outsider = uuid4()
        email = str(outsider) + "@test.invalid"
        with psycopg.connect(self.owner) as c:
            c.execute(
                "INSERT INTO app.users(id,email_normalized,display_name,status) VALUES(%s,%s,'outsider','active')",
                (outsider, email),
            )
            c.execute(
                "INSERT INTO auth.credentials(user_id,password_hash) VALUES(%s,%s)",
                (outsider, hash_password(self.password)),
            )
        self.assertEqual(
            self.client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": self.password},
                headers={"origin": self.settings.origin},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get("/api/v1/appearance-understandings/" + identity).status_code, 404
        )
        self.assertEqual(self.submit_understanding(identity).status_code, 404)
        self.assertEqual(
            self.client.get(
                "/api/v1/appearance-understandings",
                params={"product_id": self.product, "source_asset_version_id": self.version},
            ).json()["items"],
            [],
        )
