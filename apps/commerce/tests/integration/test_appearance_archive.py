"""Real tenant API and PG locking: archive metadata, never historical task rows."""

import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
from test_task_api import TaskApiFixture


class AppearanceArchiveTests(TaskApiFixture):
    def setUp(self):
        super().setUp()
        self.endpoint = "/api/v1/products/" + self.product + "/appearances"
        self.appearance = self.create_appearance()
        self.url = self.endpoint + "/" + self.appearance["id"]
        self.body["appearance_profile_id"] = self.appearance["id"]

    def create_appearance(self):
        response = self.client.post(
            self.endpoint,
            json={"name": "archive fixture", "config": {"colors": ["red"]}},
            headers=self.write_headers(),
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def archive(self, revision="1"):
        return self.client.delete(self.url, headers={**self.write_headers(), "if-match": revision})

    def assert_no_task_or_freeze(self):
        with psycopg.connect(self.owner) as c:
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
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual((balance["available"], balance["reserved"]), (16, 0))

    def test_detail_revision_archive_is_idempotent_and_preserves_readable_metadata(self):
        self.assertEqual(self.client.get(self.url).json()["config"]["colors"], ["red"])
        self.assertEqual(self.archive("2").status_code, 409)
        self.assertEqual(self.archive("invalid").status_code, 422)
        self.assertEqual(self.archive().status_code, 204)
        once = self.client.get(self.url).json()
        self.assertIsNotNone(once["archived_at"])
        self.assertEqual(once["revision"], 2)
        self.assertEqual(self.archive().status_code, 204)
        self.assertEqual(self.client.get(self.url).json(), once)
        self.assertEqual(self.client.get(self.endpoint).json()["items"], [])
        archived = self.client.get(self.endpoint, params={"include_archived": True}).json()
        self.assertEqual([x["id"] for x in archived["items"]], [self.appearance["id"]])
        changed = self.client.patch(
            self.url,
            json={"name": "no resurrection", "config": {}},
            headers={**self.write_headers(), "if-match": "2"},
        )
        self.assertEqual(changed.status_code, 404)

    def test_archive_filter_keeps_pagination_complete_without_duplicate_active_rows(self):
        expected = {self.appearance["id"]}
        for _ in range(4):
            expected.add(self.create_appearance()["id"])
        self.assertEqual(self.archive().status_code, 204)
        expected.remove(self.appearance["id"])
        seen = []
        cursor = None
        while True:
            params = {"limit": 2}
            if cursor:
                params["cursor"] = cursor
            result = self.client.get(self.endpoint, params=params)
            self.assertEqual(result.status_code, 200, result.text)
            data = result.json()
            seen += [row["id"] for row in data["items"]]
            cursor = data["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(set(seen), expected)
        self.assertEqual(len(seen), len(expected))

    def test_cross_product_and_cross_tenant_read_archive_do_not_reveal_profile(self):
        second = self.client.post(
            "/api/v1/products",
            json={"name": "second", "category": "generic"},
            headers=self.write_headers(),
        ).json()["id"]
        wrong = "/api/v1/products/" + second + "/appearances/" + self.appearance["id"]
        self.assertEqual(self.client.get(wrong).status_code, 404)
        self.assertEqual(
            self.client.delete(
                wrong, headers={**self.write_headers(), "if-match": "1"}
            ).status_code,
            404,
        )
        stranger, product, identity = uuid4(), uuid4(), uuid4()
        with psycopg.connect(self.owner) as c:
            c.execute(
                "INSERT INTO app.users(id,email_normalized,display_name,status) VALUES(%s,%s,'other','active')",
                (stranger, str(stranger) + "@test.invalid"),
            )
            c.execute(
                "INSERT INTO app.products(id,user_id,name,category) VALUES(%s,%s,'private','generic')",
                (product, stranger),
            )
            c.execute(
                "INSERT INTO app.appearance_profiles(id,user_id,product_id,name,config) VALUES(%s,%s,%s,'private','{}')",
                (identity, stranger, product),
            )
        foreign = f"/api/v1/products/{product}/appearances/{identity}"
        self.assertEqual(self.client.get(foreign).status_code, 404)
        self.assertEqual(
            self.client.delete(
                foreign, headers={**self.write_headers(), "if-match": "1"}
            ).status_code,
            404,
        )
        self.assertIsNone(self.client.get(self.url).json()["archived_at"])

    def test_archival_rejects_old_quote_submission_and_new_quote_without_financial_effects(self):
        quote = self.quote()
        self.assertEqual(self.archive().status_code, 204)
        rejected = self.submit(quote)
        self.assertEqual(rejected.status_code, 404, rejected.text)
        self.assertEqual(rejected.json()["error"]["code"], "appearance_unavailable")
        self.assertEqual(
            self.client.post(
                "/api/v1/quotes", json=self.body, headers=self.write_headers()
            ).status_code,
            404,
        )
        self.assert_no_task_or_freeze()
        with psycopg.connect(self.owner) as c:
            self.assertIsNone(
                c.execute(
                    "SELECT consumed_task_id FROM app.quotes WHERE id=%s", (quote,)
                ).fetchone()[0]
            )

    def test_existing_task_replay_and_frozen_config_survive_profile_archive(self):
        quote = self.quote()
        created = self.submit(quote)
        self.assertEqual(created.status_code, 201, created.text)
        task = created.json()["id"]
        self.assertEqual(self.archive().status_code, 204)
        replay = self.submit(quote)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["id"], task)
        with psycopg.connect(self.owner) as c:
            data = c.execute(
                "SELECT input_snapshot FROM app.generation_tasks WHERE id=%s", (task,)
            ).fetchone()[0]
        self.assertEqual(data["_execution"]["appearance"]["config"]["colors"], ["red"])

    def archive_wins_race(self, action):
        with psycopg.connect(self.owner) as archiver:
            pid = archiver.execute("SELECT pg_backend_pid()").fetchone()[0]
            archiver.execute(
                "UPDATE app.appearance_profiles SET archived_at=now(),revision=revision+1 WHERE id=%s",
                (self.appearance["id"],),
            )
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(action)
                try:
                    deadline = time.monotonic() + 5
                    with psycopg.connect(self.owner, autocommit=True) as observer:
                        blocked = False
                        while time.monotonic() < deadline:
                            blocked = observer.execute(
                                "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE %s=ANY(pg_blocking_pids(pid)))",
                                (pid,),
                            ).fetchone()[0]
                            if blocked:
                                break
                            time.sleep(0.02)
                        self.assertTrue(
                            blocked, "new quote/task must actually wait for archive lock"
                        )
                        self.assertFalse(pending.done())
                finally:
                    archiver.commit()
                response = pending.result(timeout=15)
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["code"], "appearance_unavailable")
        self.assert_no_task_or_freeze()

    def test_archive_lock_wins_against_concurrent_quote_insert(self):
        self.archive_wins_race(
            lambda: self.client.post("/api/v1/quotes", json=self.body, headers=self.write_headers())
        )
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.quotes WHERE user_id=%s", (self.user,)
                ).fetchone()[0],
                0,
            )

    def test_archive_lock_wins_against_concurrent_task_insert(self):
        quote = self.quote()
        self.archive_wins_race(lambda: self.submit(quote))
