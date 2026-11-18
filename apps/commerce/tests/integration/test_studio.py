import hashlib
import json
from io import BytesIO
from unittest.mock import patch
from uuid import UUID, uuid4
from zipfile import ZipFile

import psycopg
from PIL import Image
from test_assets import AssetFixture


class StudioTests(AssetFixture):
    def test_scope_changes_keep_stable_ids_and_archived_history(self):
        output = self.import_output()["id"]
        self.approve(output)
        original = self.project_view()
        ids = [s["id"] for s in original["slots"]]
        response = self.client.put(
            "/api/v1/studio/projects/" + original["id"] + "/slots",
            json={
                "expected_revision": original["revision"],
                "slot_ids": list(reversed(ids[1:6])),
                "additions": [{"title": "Additional view", "purpose": "product"}],
            },
            headers=self.write_headers(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        view = response.json()
        self.assertEqual([s["id"] for s in view["slots"][:5]], list(reversed(ids[1:6])))
        self.assertEqual([s["display_number"] for s in view["slots"]], list(range(1, 7)))
        self.assertIn(ids[0], [s["id"] for s in view["archived_slots"]])
        self.assertFalse(next(o for o in view["outputs"] if o["id"] == output)["eligible"])
        self.assertEqual(self.deliver([output], mode="draft").status_code, 409)
        restored = self.client.put(
            "/api/v1/studio/projects/" + original["id"] + "/slots",
            json={
                "expected_revision": view["revision"],
                "slot_ids": ids,
                "additions": [],
            },
            headers=self.write_headers(),
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual([s["id"] for s in restored.json()["slots"]], ids)

    def test_supplement_evidence_completes_ten_without_replacing_first_eight(self):
        ids = []
        for index in range(8):
            output = self.import_output(index)["id"]
            self.approve(output)
            ids.append(output)
        partial = self.deliver(ids)
        self.assertEqual(partial.status_code, 201, partial.text)
        package = self.write(
            "/skus/" + self.sku + "/references",
            dict(
                asset_version_id=self.version,
                role="packaging",
                confirmed=True,
            ),
        ).json()["id"]
        extra = {}
        for key, value in (
            ("dimensions", "Synthetic 100 mm"),
            ("package_list", "Synthetic product only"),
        ):
            response = self.write(
                "/skus/" + self.sku + "/facts",
                dict(
                    fact_key=key,
                    label=key,
                    value=value,
                    status="confirmed",
                    reference_ids=[package],
                ),
            )
            self.assertEqual(response.status_code, 201, response.text)
            extra[key] = response.json()["id"]
        for index, key in ((8, "dimensions"), (9, "package_list")):
            planned = self.plan(
                index, fact_version_ids=[self.fact, extra[key]], reference_ids=[self.ref, package]
            )
            self.assertEqual(planned.status_code, 201, planned.text)
            output = self.import_output(index, plan=planned.json())["id"]
            self.approve(output)
            ids.append(output)
        complete = self.deliver(ids)
        self.assertEqual(complete.status_code, 201, complete.text)
        self.assertEqual(complete.json()["manifest"]["scope"], "complete")
        self.assertEqual(complete.json()["manifest"]["delivered_count"], 10)
        self.assertEqual(
            [s["current_output_id"] for s in self.project_view()["slots"][:8]], ids[:8]
        )
        self.assertEqual(self.project_view()["deliveries"][1]["manifest"]["scope"], "partial")

    def import_output(self, slot_index=0, plan=None, key=None):
        plan = plan or self.plan(slot_index).json()
        result = self.client.post(
            "/api/v1/studio/projects/"
            + self.project["id"]
            + "/slots/"
            + self.project["slots"][slot_index]["id"]
            + "/outputs",
            json=dict(
                plan_id=plan["id"],
                source_asset_version_id=self.version,
                route="manual_import",
                source_note="Synthetic fixture manually imported; not AI generated",
            ),
            headers={**self.write_headers(), "idempotency-key": key or str(uuid4())},
        )
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()

    def approve(self, output_id, decision="passed"):
        result = self.write(
            "/outputs/" + output_id + "/reviews",
            dict(
                expected_revision=self.project_view()["revision"],
                decision=decision,
                critical=decision == "rework",
                checks={
                    k: "failed" if decision == "rework" and k == "structure" else "passed"
                    for k in ("identity", "structure", "accessories", "copy", "scene", "technical")
                },
                notes="Compared synthetic output with synthetic source evidence",
            ),
        )
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()

    def deliver(self, ids, mode="formal", key=None):
        return self.client.post(
            "/api/v1/studio/projects/" + self.project["id"] + "/deliveries",
            json=dict(expected_revision=self.project_view()["revision"], mode=mode, output_ids=ids),
            headers={**self.write_headers(), "idempotency-key": key or str(uuid4())},
        )

    def write(self, path, body):
        return self.client.post("/api/v1/studio" + path, json=body, headers=self.write_headers())

    def setUp(self):
        super().setUp()
        stream = BytesIO()
        Image.new("RGB", (256, 256), "red").save(stream, "PNG")
        upload = self.upload(stream.getvalue())
        self.version = self.complete(upload).json()["version_id"]
        self.sku = self.write(
            "/skus", dict(product_id=self.product, code="A", name="Synthetic A")
        ).json()["id"]
        self.ref = self.write(
            "/skus/" + self.sku + "/references",
            dict(
                asset_version_id=self.version, role="product", physical_face="FRONT", confirmed=True
            ),
        ).json()["id"]
        self.fact = self.write(
            "/skus/" + self.sku + "/facts",
            dict(
                fact_key="appearance",
                label="外观",
                value="合成红色方块",
                status="confirmed",
                reference_ids=[self.ref],
            ),
        ).json()["id"]
        result = self.write(
            "/projects",
            dict(
                sku_id=self.sku,
                title="Synthetic suite",
                platform="amazon",
                marketplace="DE",
                language="de",
                category="generic",
                image_use="GALLERY",
                slot_count=10,
                output=dict(width=256, height=256, format="png"),
            ),
        )
        self.assertEqual(result.status_code, 201, result.text)
        self.project = result.json()

    def project_view(self):
        return self.client.get("/api/v1/studio/projects/" + self.project["id"]).json()

    def plan(self, slot=0, **changes):
        project = self.project_view()
        body = dict(
            expected_revision=project["revision"],
            goal="Evidence-bound synthetic image",
            copy="Confirmed copy",
            fact_version_ids=[self.fact],
            reference_ids=[self.ref],
            allowed_faces=["FRONT"],
        )
        body.update(changes)
        return self.write(
            "/projects/" + project["id"] + "/slots/" + project["slots"][slot]["id"] + "/plans", body
        )

    def test_eight_ready_two_missing_and_custom_market(self):
        self.assertEqual(
            [s["readiness"]["status"] for s in self.project["slots"]],
            ["ready"] * 8 + ["missing"] * 2,
        )
        self.assertEqual(
            (self.project["marketplace"], self.project["language"], self.project["image_use"]),
            ("DE", "de", "GALLERY"),
        )
        self.assertEqual(self.plan(8).status_code, 409)
        self.assertEqual(self.plan(9).status_code, 409)
        self.assertEqual(self.plan().status_code, 201)

    def test_unknown_face_and_cross_sku_plan_are_rejected(self):
        self.assertEqual(self.plan(allowed_faces=["BOTTOM"]).status_code, 422)
        other = self.write(
            "/skus", dict(product_id=self.product, code="B", name="Synthetic B")
        ).json()["id"]
        foreign = self.write(
            "/skus/" + other + "/references",
            dict(
                asset_version_id=self.version, role="product", physical_face="FRONT", confirmed=True
            ),
        ).json()["id"]
        self.assertEqual(self.plan(reference_ids=[foreign]).status_code, 422)
        with psycopg.connect(self.owner) as conn:
            conn.execute("SET SESSION AUTHORIZATION app_api")
            conn.execute("SELECT set_config('app.user_id',%s,true)", (str(self.user),))
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                conn.execute(
                    "INSERT INTO app.studio_fact_sources VALUES(%s,%s,%s,%s)",
                    (self.user, UUID(self.sku), UUID(self.fact), UUID(foreign)),
                )

    def test_fact_revision_marks_only_dependent_slots_and_keeps_history(self):
        first = self.plan().json()
        result = self.write(
            "/skus/" + self.sku + "/facts",
            dict(
                fact_key="appearance",
                expected_revision=1,
                label="外观",
                value="修订后的合成红色方块",
                status="confirmed",
                reference_ids=[self.ref],
            ),
        )
        self.assertEqual(result.status_code, 201, result.text)
        self.assertEqual(
            [r["slot_id"] for r in result.json()["impacted_slots"]],
            [self.project["slots"][0]["id"]],
        )
        view = self.project_view()
        self.assertTrue(view["slots"][0]["plan_stale"])
        self.assertFalse(view["slots"][1]["plan_stale"])
        self.assertEqual(view["slots"][0]["plan"]["facts"][0]["id"], self.fact)
        self.assertEqual(view["slots"][0]["plan_id"], first["id"])

    def test_other_user_cannot_read_project_or_sku(self):
        with psycopg.connect(self.owner) as conn:
            conn.execute("SET SESSION AUTHORIZATION app_api")
            conn.execute("SELECT set_config('app.user_id',%s,true)", (str(uuid4()),))
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) FROM app.studio_projects WHERE id=%s",
                    (UUID(self.project["id"]),),
                ).fetchone()[0],
                0,
            )

            self.assertEqual(
                conn.execute(
                    "SELECT count(*) FROM app.studio_skus WHERE id=%s", (UUID(self.sku),)
                ).fetchone()[0],
                0,
            )

    def test_manual_import_pending_review_and_critical_defect_blocks_formal(self):
        output = self.import_output()["id"]
        self.assertEqual(self.deliver([output]).status_code, 409)
        self.approve(output, decision="rework")
        self.assertEqual(self.deliver([output]).status_code, 409)
        self.assertEqual(self.deliver([output], mode="draft").status_code, 201)
        self.approve(output)
        self.assertEqual(self.deliver([output]).status_code, 201)

    def test_replacement_does_not_inherit_review_and_old_version_is_not_current(self):
        plan = self.plan().json()
        old = self.import_output(plan=plan)["id"]
        self.approve(old)
        new = self.import_output(plan=plan)["id"]
        view = self.project_view()
        self.assertFalse(next(o for o in view["outputs"] if o["id"] == new)["eligible"])
        self.assertFalse(next(o for o in view["outputs"] if o["id"] == old)["eligible"])
        self.assertEqual(self.deliver([new]).status_code, 409)
        self.assertEqual(self.deliver([old]).status_code, 409)

    def test_eight_file_delivery_manifest_and_case_return(self):
        ids = []
        for i in range(8):
            output = self.import_output(i)["id"]
            self.approve(output)
            ids.append(output)
        result = self.deliver(ids)
        self.assertEqual(result.status_code, 201, result.text)
        delivery = result.json()
        self.assertEqual(
            (
                delivery["manifest"]["delivered_count"],
                delivery["manifest"]["total_slots"],
                delivery["manifest"]["scope"],
            ),
            (8, 10, "partial"),
        )
        self.assertEqual([s["slot_number"] for s in delivery["manifest"]["pending_slots"]], [9, 10])
        downloaded = self.client.get("/api/v1/studio/deliveries/" + delivery["id"] + "/download")
        self.assertEqual(
            downloaded.status_code,
            200,
            downloaded.text[:100] if downloaded.status_code != 200 else "",
        )
        self.assertEqual(hashlib.sha256(downloaded.content).hexdigest(), delivery["sha256"])
        with ZipFile(BytesIO(downloaded.content)) as package:
            manifest = json.loads(package.read("manifest.json"))
            self.assertEqual(len(manifest["files"]), 8)
            for item in manifest["files"]:
                self.assertEqual(
                    hashlib.sha256(package.read(item["filename"])).hexdigest(), item["sha256"]
                )
                self.assertEqual(item["source_files"], [])
        saved = self.write(
            "/projects/" + self.project["id"] + "/cases",
            dict(
                delivery_id=delivery["id"],
                title="Synthetic case",
                lessons="Keep unknown faces unknown.",
            ),
        )
        self.assertEqual(saved.status_code, 201, saved.text)
        self.assertIsNone(saved.json()["marketing_data"])

    def test_fact_update_during_export_does_not_publish_stale_delivery(self):
        output = self.import_output()["id"]
        self.approve(output)
        from visual_platform.services import studio_files

        original = studio_files.archive

        def changed(manifest, files):
            response = self.write(
                "/skus/" + self.sku + "/facts",
                dict(
                    fact_key="appearance",
                    expected_revision=1,
                    label="外观",
                    value="Changed synthetic evidence",
                    status="confirmed",
                    reference_ids=[self.ref],
                ),
            )
            self.assertEqual(response.status_code, 201, response.text)
            return original(manifest, files)

        with patch.object(studio_files, "archive", side_effect=changed):
            response = self.deliver([output])
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.project_view()["deliveries"][0]["status"], "failed")

    def test_import_replay_returns_same_output(self):
        plan = self.plan().json()
        key = str(uuid4())
        first = self.import_output(plan=plan, key=key)
        replay = self.import_output(plan=plan, key=key)
        self.assertEqual(first["id"], replay["id"])
        self.assertTrue(replay["idempotent_replay"])
