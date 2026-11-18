"""Nonempty Qwen restore fixture, isolated synthetic user and explicit mock transports."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import psycopg
from fastapi.testclient import TestClient
from PIL import Image
from restore_protocol_fixture import run_cost_review_fixture

from visual_platform.api.main import create_app
from visual_platform.core.identity import hash_password
from visual_platform.db.result import required_row
from visual_platform.providers.authorization import (
    BailianSmokeAuthorization,
    canonical_authorization,
)
from visual_platform.providers.runtime import ProviderRuntime, credential_fingerprint
from visual_platform.services.backup import require
from visual_platform.services.provider_budget import (
    provider_budget_snapshot,
    provider_quote_review,
    register_provider_budget,
)
from visual_platform.services.rule_operations import assert_operator
from visual_platform.services.runtime_quote import (
    prepare_runtime_quote,
    register_runtime_configuration,
    registered_runtime_context,
)


def guard(profile):
    require(profile.get("PROVIDER_PROFILE") == "offline", "Qwen drill is offline only")
    require(
        all(
            urlsplit(profile[name]).hostname in ("127.0.0.1", "localhost")
            for name in (
                "APP_ORIGIN",
                "API_DATABASE_URL",
                "AUTH_DATABASE_URL",
                "ADMIN_DATABASE_URL",
                "WORKER_DATABASE_URL",
                "S3_ENDPOINT",
            )
        ),
        "Qwen drill requires an owned loopback profile",
    )


def admin(profile, query, values):
    with psycopg.connect(profile["ADMIN_DATABASE_URL"]) as connection:
        assert_operator(connection)
        return required_row(connection.execute(query, values))[0]


def login(client, profile, state):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": state["email"], "password": state["password"]},
        headers={"origin": profile["APP_ORIGIN"]},
    )
    require(response.status_code == 200, "Qwen fixture login failed")
    return {"origin": profile["APP_ORIGIN"], "x-csrf-token": client.cookies["visual_csrf"]}


def prepare_qwen_restore(profile, settings):
    guard(profile)
    user = uuid4()
    state: dict[str, Any] = {
        "user_id": str(user),
        "email": "qwen-restore-" + uuid4().hex + "@example.invalid",
        "password": uuid4().hex + uuid4().hex,
    }
    admin(
        profile,
        "SELECT app.create_private_user(%s,%s,'synthetic Qwen restore',%s)",
        (user, state["email"], hash_password(state["password"])),
    )
    admin(
        profile,
        "SELECT app.grant_credits(%s,%s,32,'synthetic Qwen restore credits')",
        (user, uuid4()),
    )
    key_sha = credential_fingerprint("offline-restore-fixture")
    runtime = ProviderRuntime.model_validate(
        {
            "schema_version": 2,
            "version": "restore-qwen-" + uuid4().hex,
            "generate": {
                "provider": "aliyun_bailian",
                "model": "qwen-image-3.0",
                "endpoint_origin": "https://ws-synthetic.cn-beijing.maas.aliyuncs.com",
                "merchant_fingerprint": "d" * 64,
                "credential_fingerprint": key_sha,
                "billing_group": "synthetic-qwen-restore",
            },
            "detect": {
                "provider": "google_gemini",
                "model": "gemini-3.8-flash",
                "endpoint_origin": "https://generativelanguage.googleapis.com",
                "merchant_fingerprint": "e" * 64,
                "credential_fingerprint": key_sha,
                "billing_group": "synthetic-qwen-restore",
            },
        },
        strict=True,
    )
    register_runtime_configuration(
        profile["ADMIN_DATABASE_URL"],
        runtime,
        runtime.digest(),
        uuid4(),
        "synthetic Qwen restore metadata, not live permission",
    )
    state["runtime_sha256"] = runtime.digest()
    with TestClient(create_app(settings)) as client:
        headers = login(client, profile, state)
        product = client.post(
            "/api/v1/products",
            json={"name": "synthetic Qwen restore product", "category": "generic"},
            headers=headers,
        )
        require(product.status_code == 201, "Qwen fixture product failed")
        product_id = product.json()["id"]
        stream = BytesIO()
        Image.new("RGB", (512, 512), (201, 15, 35)).save(stream, "PNG")
        image = stream.getvalue()
        upload = client.post(
            "/api/v1/assets/upload-intents",
            json={
                "product_id": product_id,
                "filename": "qwen-restore.png",
                "mime": "image/png",
                "size": len(image),
            },
            headers=headers,
        )
        require(upload.status_code == 201, "Qwen fixture upload intent failed")
        intent = upload.json()
        require(
            urlsplit(intent["upload_url"]).hostname in ("localhost", "127.0.0.1"),
            "Qwen fixture upload left loopback",
        )
        require(
            httpx.put(intent["upload_url"], content=image, headers=intent["headers"]).status_code
            == 200,
            "Qwen fixture upload failed",
        )
        completed = client.post(
            "/api/v1/assets/upload-intents/" + intent["intent_id"] + "/complete", headers=headers
        )
        require(completed.status_code == 200, "Qwen fixture canonical source failed")
        source = completed.json()["version_id"]
        body = {
            "product_id": product_id,
            "source_asset_version_id": source,
            "generation": {
                "type": "scene",
                "prompt": "synthetic Qwen recovery only",
                "output": {"width": 512, "height": 512, "format": "png"},
                "protection_mode": "pixel_exact",
                "regions": [
                    {
                        "source_x": 8,
                        "source_y": 8,
                        "width": 4,
                        "height": 4,
                        "target_x": 8,
                        "target_y": 8,
                    }
                ],
            },
            "targets": [
                {
                    "platform": "amazon",
                    "marketplace": "US",
                    "category": "generic",
                    "image_type": "MAIN",
                }
            ],
        }
        original = client.post("/api/v1/quotes", json=body, headers=headers)
        require(original.status_code == 201, "Qwen fixture source quote failed")
        prepared = prepare_runtime_quote(
            profile["ADMIN_DATABASE_URL"],
            user,
            UUID(original.json()["quote_id"]),
            runtime,
            runtime.digest(),
            uuid4(),
            "synthetic Qwen restore quote",
        )
        quote = UUID(prepared["quote_id"])
        review = provider_quote_review(profile["ADMIN_DATABASE_URL"], user, quote)
        now = datetime.now(timezone.utc)
        scope = BailianSmokeAuthorization.model_validate_json(
            json.dumps(
                {
                    "schema_version": 4,
                    "authorization_id": str(uuid4()),
                    "user_id": str(user),
                    "product_id": product_id,
                    "source_asset_version_id": source,
                    "authorized_by": "synthetic Qwen restore, no spending permission",
                    "authorized_at": (now - timedelta(seconds=1)).isoformat(),
                    "expires_at": (now + timedelta(hours=1)).isoformat(),
                    "source_path": "synthetic.png",
                    "source_sha256": review["input_snapshot"]["_execution"]["source_sha256"],
                    "quote_input_sha256": review["input_sha256"],
                    "generation": body["generation"],
                    "targets": body["targets"],
                    "step_budgets": {
                        "generate": {"currency": "CNY", "ceiling": "1"},
                        "detect": {"currency": "USD", "ceiling": "0.1"},
                    },
                    "currency_totals": [
                        {"currency": "CNY", "ceiling": "1"},
                        {"currency": "USD", "ceiling": "0.1"},
                    ],
                }
            )
        )
        state["budget_id"] = register_provider_budget(
            profile["ADMIN_DATABASE_URL"],
            quote,
            scope,
            hashlib.sha256(canonical_authorization(scope)).hexdigest(),
            uuid4(),
            "synthetic Qwen restore budget",
        )
        created = client.post(
            "/api/v1/tasks",
            json={**body, "quote_id": str(quote)},
            headers={**headers, "idempotency-key": str(uuid4())},
        )
        require(created.status_code == 201, "Qwen fixture task failed")
        state["task_id"] = created.json()["id"]
        run = run_cost_review_fixture(
            profile,
            settings,
            UUID(state["task_id"]),
            runtime.execution_binding()["provider_accounts"],
            detect_success=True,
            runtime=runtime,
        )
        require(
            run["result"] == "succeeded" and run["posts"] == {"generate": 1, "detect": 1},
            "Qwen source fixture failed",
        )
        state["generation_integrity"] = admin(
            profile, "SELECT ops.generation_archive_integrity(%s,%s)", (user, state["task_id"])
        )
        state["query_integrity"] = admin(
            profile, "SELECT ops.qwen_result_integrity(%s,%s)", (user, state["task_id"])
        )
        require(
            state["query_integrity"] is not None
            and all(
                state["query_integrity"][key]
                for key in (
                    "submission_bound",
                    "generated_image_bound",
                    "wire_hash_valid",
                    "canonical_hash_valid",
                )
            ),
            "Qwen source query integrity failed",
        )
        report = client.get("/api/v1/tasks/" + state["task_id"] + "/compliance")
        require(report.status_code == 200, "Qwen source report failed")
        state["final_sha256"] = report.json()["final_sha256"]
        require(
            state["final_sha256"] != state["query_integrity"]["generated_sha256"],
            "Qwen protected final must remain distinct from raw generated image",
        )
        balance = client.get("/api/v1/credits/balance").json()
        state["balance"] = [balance[key] for key in ("available", "reserved", "spent_total")]
        require(state["balance"] == [16, 0, 16], "Qwen source wallet mismatch")
        completed_budget = provider_budget_snapshot(
            profile["ADMIN_DATABASE_URL"], UUID(state["budget_id"])
        )
        require(completed_budget["state"] == "closed", "completed Qwen budget was not closed")
        state["step_limits"] = completed_budget["step_limits"]
    return state


def verify_qwen_restore(profile, settings, state):
    guard(profile)
    user = UUID(state["user_id"])
    task = UUID(state["task_id"])
    generation = admin(profile, "SELECT ops.generation_archive_integrity(%s,%s)", (user, task))
    query = admin(profile, "SELECT ops.qwen_result_integrity(%s,%s)", (user, task))
    require(
        generation == state["generation_integrity"] and query == state["query_integrity"],
        "restored Qwen wire/intent/image/meter proof changed",
    )
    require(
        query["billing_usage_integrity"] == "complete"
        and query["quantities"]
        == {"qima_input_1k": 1, "qima_input_2k": 0, "qima_output_1k": 1, "qima_output_2k": 0},
        "restored Qwen meter mismatch",
    )
    require(
        not (Path(profile["FAKE_PROVIDER_DIR"]) / "protocol-restore" / str(task)).exists(),
        "Qwen local supplier files unexpectedly restored",
    )
    context = registered_runtime_context(profile["ADMIN_DATABASE_URL"], state["runtime_sha256"])
    runtime = ProviderRuntime.model_validate(context["document"], strict=True)
    run = run_cost_review_fixture(
        profile,
        settings,
        task,
        runtime.execution_binding()["provider_accounts"],
        detect_success=True,
        runtime=runtime,
    )
    require(
        run["result"] == "not_claimed" and run["posts"] == {"generate": 0, "detect": 0},
        "restored completed Qwen task resubmitted",
    )
    budget = provider_budget_snapshot(profile["ADMIN_DATABASE_URL"], UUID(state["budget_id"]))
    require(
        budget["state"] == "closed" and budget["step_limits"] == state["step_limits"],
        "restored Qwen budget was reauthorized or units changed",
    )
    with TestClient(create_app(settings)) as client:
        login(client, profile, state)
        report = client.get("/api/v1/tasks/" + str(task) + "/compliance")
        require(
            report.status_code == 200 and report.json()["final_sha256"] == state["final_sha256"],
            "restored Qwen report mismatch",
        )
        download = client.get("/api/v1/tasks/" + str(task) + "/download", follow_redirects=False)
        require(
            download.status_code == 302
            and urlsplit(download.headers["location"]).hostname in ("localhost", "127.0.0.1"),
            "Qwen private download failed",
        )
        data = httpx.get(download.headers["location"]).content
        require(
            hashlib.sha256(data).hexdigest() == state["final_sha256"],
            "Qwen restored final hash mismatch",
        )
        with Image.open(BytesIO(data)) as image:
            require(
                image.convert("RGB").crop((8, 8, 12, 12)).tobytes()
                == Image.new("RGB", (4, 4), (201, 15, 35)).tobytes(),
                "Qwen restored protection pixels changed",
            )
        balance = client.get("/api/v1/credits/balance").json()
        require(
            [balance[key] for key in ("available", "reserved", "spent_total")] == state["balance"],
            "restored Qwen wallet changed",
        )
