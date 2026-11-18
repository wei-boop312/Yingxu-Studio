from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
from test_auth_api import AuthApiFixture


class TaskApiFixture(AuthApiFixture):
    def setUp(self):
        super().setUp()
        self.login()
        self.product = self.client.post(
            "/api/v1/products",
            json={"name": "test", "category": "generic"},
            headers=self.write_headers(),
        ).json()["id"]
        self.version = uuid4()
        asset = uuid4()
        with psycopg.connect(self.owner) as c:
            c.execute(
                "INSERT INTO app.assets(id,user_id,product_id,kind,status) VALUES(%s,%s,%s,'original','uploading')",
                (asset, self.user, self.product),
            )
            c.execute(
                "INSERT INTO app.asset_versions(id,user_id,product_id,asset_id,version_number,bucket,object_key,sha256,mime,width,height,byte_size,color_space,canonical) VALUES(%s,%s,%s,%s,1,'test',%s,%s,'image/png',256,256,100,'sRGB',true)",
                (self.version, self.user, self.product, asset, str(uuid4()), "a" * 64),
            )
            c.execute(
                "UPDATE app.assets SET status='ready',current_version_id=%s WHERE id=%s",
                (self.version, asset),
            )
            c.execute("SET LOCAL ROLE ops_admin")
            c.execute("SELECT app.grant_credits(%s,%s,16,'test')", (self.user, uuid4()))
        self.body = {
            "product_id": self.product,
            "source_asset_version_id": str(self.version),
            "generation": {
                "type": "scene",
                "prompt": "test scene",
                "output": {"width": 256, "height": 256, "format": "png"},
                "protection_mode": "pixel_exact",
                "regions": [
                    {
                        "source_x": 0,
                        "source_y": 0,
                        "width": 1,
                        "height": 1,
                        "target_x": 0,
                        "target_y": 0,
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

    def quote(self):
        r = self.client.post("/api/v1/quotes", json=self.body, headers=self.write_headers())
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["quote_id"]

    def submit(self, q, key="test", body=None):
        return self.client.post(
            "/api/v1/tasks",
            json={**(body or self.body), "quote_id": q},
            headers={**self.write_headers(), "idempotency-key": key},
        )


class TaskApiTests(TaskApiFixture):
    def test_http_concurrent_replay_binding_cancel(self):
        q = self.quote()
        barrier = Barrier(20)

        def compete(_):
            barrier.wait(timeout=10)
            return self.submit(q)

        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(compete, range(20)))
        self.assertTrue(
            all(r.status_code in (200, 201) for r in results), [r.text for r in results]
        )
        self.assertEqual(sum(r.status_code == 201 for r in results), 1)
        ids = {r.json()["id"] for r in results}
        self.assertEqual(len(ids), 1)
        changed = {**self.body, "generation": {**self.body["generation"], "prompt": "changed"}}
        self.assertEqual(self.submit(q, body=changed).status_code, 409)
        self.assertEqual(self.submit(q, key="new").status_code, 409)
        task = ids.pop()
        for _ in range(2):
            self.assertEqual(
                self.client.post(
                    "/api/v1/tasks/" + task + "/cancel", headers=self.write_headers()
                ).status_code,
                200,
            )
        self.assertEqual(self.client.get("/api/v1/credits/balance").json()["available"], 16)

    def test_expired_quote_replay_immutability(self):
        q = self.quote()
        task = self.submit(q).json()["id"]
        with psycopg.connect(self.owner) as c:
            c.execute("SET LOCAL session_replication_role=replica")
            c.execute("UPDATE app.quotes SET expires_at=now()-interval '1 hour' WHERE id=%s", (q,))
        self.assertEqual(self.submit(q).json()["id"], task)
        with psycopg.connect(self.owner) as c:
            with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
                c.execute("UPDATE app.quotes SET credits=1 WHERE id=%s", (q,))

    def test_racing_quotes(self):
        qs = [self.quote(), self.quote()]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda q: self.submit(q, key=q), qs))
        self.assertEqual(sorted(r.status_code for r in results), [201, 409])

    def test_two_products_compete_for_one_wallet_reservation(self):
        other = self.client.post(
            "/api/v1/products",
            json={"name": "independent wallet contender", "category": "generic"},
            headers=self.write_headers(),
        ).json()["id"]
        asset = uuid4()
        version = uuid4()
        with psycopg.connect(self.owner) as c:
            c.execute(
                "INSERT INTO app.assets(id,user_id,product_id,kind,status) VALUES(%s,%s,%s,'original','uploading')",
                (asset, self.user, other),
            )
            c.execute(
                "INSERT INTO app.asset_versions(id,user_id,product_id,asset_id,version_number,bucket,object_key,sha256,mime,width,height,byte_size,color_space,canonical) VALUES(%s,%s,%s,%s,1,'test',%s,%s,'image/png',256,256,100,'sRGB',true)",
                (version, self.user, other, asset, str(uuid4()), "a" * 64),
            )
            c.execute(
                "UPDATE app.assets SET status='ready',current_version_id=%s WHERE id=%s",
                (version, asset),
            )
        first = self.quote()
        body = {**self.body, "product_id": other, "source_asset_version_id": str(version)}
        quoted = self.client.post("/api/v1/quotes", json=body, headers=self.write_headers())
        self.assertEqual(quoted.status_code, 201, quoted.text)
        attempts = [(first, self.body), (quoted.json()["quote_id"], body)]
        barrier = Barrier(2)

        def compete(item):
            barrier.wait(timeout=10)
            return self.submit(item[0], key=item[0], body=item[1])

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(compete, attempts))
        self.assertEqual(sorted(r.status_code for r in results), [201, 409])
        rejected = next(r for r in results if r.status_code == 409)
        self.assertEqual(rejected.json()["error"]["code"], "insufficient_credits")
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual((balance["available"], balance["reserved"]), (0, 16))
        accepted = next(r.json()["id"] for r in results if r.status_code == 201)
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.credit_transactions WHERE user_id=%s AND type='freeze'",
                    (self.user,),
                ).fetchone()[0],
                1,
            )
        self.assertEqual(
            self.client.post(
                "/api/v1/tasks/" + accepted + "/cancel", headers=self.write_headers()
            ).status_code,
            200,
        )
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual((balance["available"], balance["reserved"]), (16, 0))

    def test_size_and_archive(self):
        self.body["generation"]["output"]["width"] = 257
        response = self.client.post("/api/v1/quotes", json=self.body, headers=self.write_headers())
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "pixel_exact_size_mismatch")
        self.body["generation"]["output"]["width"] = 256
        self.client.delete("/api/v1/products/" + self.product, headers=self.write_headers())
        self.assertEqual(
            self.client.post(
                "/api/v1/quotes", json=self.body, headers=self.write_headers()
            ).status_code,
            404,
        )

    def test_outbox_failure_rolls_back_task_wallet_quote(self):
        from psycopg import sql

        q = self.quote()
        name = "fault_" + uuid4().hex
        with psycopg.connect(self.owner) as c:
            body = (
                "BEGIN IF NEW.user_id='"
                + str(self.user)
                + "'::uuid THEN RAISE EXCEPTION 'injected_outbox_failure'; END IF; RETURN NEW; END"
            )
            c.execute(
                sql.SQL("CREATE FUNCTION ops.{}() RETURNS trigger LANGUAGE plpgsql AS {}").format(
                    sql.Identifier(name), sql.Literal(body)
                )
            )
            c.execute(
                sql.SQL(
                    "CREATE TRIGGER {} BEFORE INSERT ON ops.outbox FOR EACH ROW EXECUTE FUNCTION ops.{}()"
                ).format(sql.Identifier(name), sql.Identifier(name))
            )
        try:
            self.assertEqual(self.submit(q).status_code, 503)
            with psycopg.connect(self.owner) as c:
                self.assertEqual(
                    c.execute(
                        "SELECT available,reserved FROM app.wallets WHERE user_id=%s", (self.user,)
                    ).fetchone(),
                    (16, 0),
                )
                self.assertIsNone(
                    c.execute(
                        "SELECT consumed_task_id FROM app.quotes WHERE id=%s", (q,)
                    ).fetchone()[0]
                )
                self.assertEqual(
                    c.execute(
                        "SELECT count(*) FROM app.generation_tasks WHERE user_id=%s", (self.user,)
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    c.execute(
                        "SELECT count(*) FROM app.credit_transactions WHERE user_id=%s AND type='freeze'",
                        (self.user,),
                    ).fetchone()[0],
                    0,
                )
        finally:
            with psycopg.connect(self.owner) as c:
                c.execute(sql.SQL("DROP TRIGGER {} ON ops.outbox").format(sql.Identifier(name)))
                c.execute(sql.SQL("DROP FUNCTION ops.{}()").format(sql.Identifier(name)))
        self.assertEqual(self.submit(q).status_code, 201)

    def test_appearance_snapshot_survives_edit(self):
        a = self.client.post(
            "/api/v1/products/" + self.product + "/appearances",
            json={"name": "original", "config": {"colors": ["red"]}},
            headers=self.write_headers(),
        )
        self.assertEqual(a.status_code, 201, a.text)
        aid = a.json()["id"]
        self.body["appearance_profile_id"] = aid
        q = self.quote()
        result = self.client.patch(
            "/api/v1/products/" + self.product + "/appearances/" + aid,
            json={"name": "changed", "config": {"colors": ["blue"]}},
            headers={**self.write_headers(), "if-match": "1"},
        )
        self.assertEqual(result.status_code, 200, result.text)
        task = self.submit(q).json()["id"]
        with psycopg.connect(self.owner) as c:
            snapshot = c.execute(
                "SELECT input_snapshot FROM app.generation_tasks WHERE id=%s", (task,)
            ).fetchone()[0]
        self.assertEqual(snapshot["_execution"]["appearance"]["config"]["colors"], ["red"])
        self.assertEqual(snapshot["_execution"]["rules"][0]["evidence_status"], "unverified")
        self.assertEqual(
            self.client.get("/api/v1/rules/amazon").json()["evidence_status"], "unverified"
        )
