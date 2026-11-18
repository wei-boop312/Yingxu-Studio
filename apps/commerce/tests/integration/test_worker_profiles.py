"""Real PG/MQ/Worker profile isolation; supplier HTTP remains explicit MockTransport."""

import asyncio
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import UUID, uuid4

import psycopg
from provider_budget_fixture import ProviderBudgetFixture
from psycopg import sql
from test_offline_pipeline import PipelineFixture

from visual_platform.db.pool import open_pool
from visual_platform.events.broker import Broker, Delivery
from visual_platform.events.publisher import Publisher


class WorkerProfileTests(ProviderBudgetFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dispatch_role = "profile_dispatch_" + uuid4().hex
        password = secrets.token_urlsafe(32)
        with psycopg.connect(cls.owner) as c:
            c.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
                ).format(sql.Identifier(cls.dispatch_role), sql.Literal(password))
            )
            c.execute(
                sql.SQL("GRANT ops_dispatcher TO {}").format(sql.Identifier(cls.dispatch_role))
            )
        endpoint = urlsplit(cls.owner)
        cls.dispatch_dsn = urlunsplit(
            (
                endpoint.scheme,
                quote(cls.dispatch_role)
                + ":"
                + quote(password)
                + "@"
                + endpoint.hostname
                + ":"
                + str(endpoint.port or 5432),
                endpoint.path,
                endpoint.query,
                endpoint.fragment,
            )
        )

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(cls.owner) as c:
            c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(cls.dispatch_role)))
        super().tearDownClass()

    def state(self, task):
        with psycopg.connect(self.owner) as c:
            return c.execute(
                "SELECT status,phase,revision,lease_owner,fencing_token,credits_status FROM app.generation_tasks WHERE id=%s",
                (task,),
            ).fetchone()

    def test_fake_worker_never_leases_refunds_or_quarantines_protocol_task(self):
        task = self.submit(self.bind)
        before = self.state(task)
        budget = self.snapshot()
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual(self.run_worker(task), "not_claimed")
        self.assertEqual(self.state(task), before)
        self.assertEqual(self.snapshot(), budget)
        self.assertEqual(self.client.get("/api/v1/credits/balance").json(), balance)
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (0, 0))
        self.assertEqual(self.run_protocol(task), "succeeded")

    def test_protocol_worker_never_leases_default_fake_task(self):
        task = PipelineFixture.submit(self)
        before = self.state(task)
        self.assertEqual(self.run_protocol(task), "not_claimed")
        self.assertEqual(self.state(task), before)
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (0, 0))
        self.assertEqual(self.run_worker(task), "succeeded")

    def test_legacy_two_argument_claim_is_fake_only_and_explicit_profile_can_claim(self):
        task = self.submit(self.bind)
        with psycopg.connect(self.worker_dsn) as c:
            self.assertIsNone(
                c.execute("SELECT ops.claim_task(%s,'legacy-worker')", (task,)).fetchone()[0]
            )
            claim = c.execute(
                "SELECT ops.claim_task(%s,'explicit-worker','offline_protocol_mock')", (task,)
            ).fetchone()[0]
            self.assertEqual(claim["status"], "processing")
            c.execute(
                "SELECT app.yield_lease(%s,'explicit-worker',%s)", (task, claim["fencing_token"])
            )
        self.assertEqual(self.run_protocol(task), "succeeded")

    def test_mixed_concurrent_claims_have_one_correct_profile_winner(self):
        task = self.submit(self.bind)

        def claim(index):
            profile = "offline" if index % 2 == 0 else "offline_protocol_mock"
            owner = "profile-race-" + str(index)
            with psycopg.connect(self.worker_dsn) as c:
                result = c.execute(
                    "SELECT ops.claim_task(%s,%s,%s)", (task, owner, profile)
                ).fetchone()[0]
            return profile, owner, result

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(claim, range(8)))
        self.assertTrue(
            all(result is None for profile, _, result in results if profile == "offline")
        )
        winners = [(owner, result) for _, owner, result in results if result is not None]
        self.assertEqual(len(winners), 1)
        owner, result = winners[0]
        with psycopg.connect(self.worker_dsn) as c:
            c.execute("SELECT app.yield_lease(%s,%s,%s)", (task, owner, result["fencing_token"]))
        self.assertEqual(self.run_protocol(task), "succeeded")

    def test_manual_barrier_and_unrecognized_profile_cannot_be_bypassed(self):
        task = self.submit(self.bind)
        with psycopg.connect(self.owner) as c:
            c.execute("UPDATE ops.task_dispatch SET state='manual' WHERE task_id=%s", (task,))
        before = self.state(task)
        with psycopg.connect(self.worker_dsn) as c:
            self.assertIsNone(
                c.execute(
                    "SELECT ops.claim_task(%s,'explicit','offline_protocol_mock')", (task,)
                ).fetchone()[0]
            )
            with self.assertRaisesRegex(psycopg.errors.RaiseException, "invalid_worker_profile"):
                c.execute("SELECT ops.claim_task(%s,'invalid','live')", (task,))
        self.assertEqual(self.state(task), before)

    def test_protocol_notification_goes_only_to_manual_queue_and_default_delivery_rejects_it(self):
        task = self.submit(self.bind)
        before = self.state(task)

        async def exercise():
            broker = await Broker.connect(os.environ["TEST_AMQP_URL"], "profile-" + uuid4().hex)
            pool = await open_pool(self.dispatch_dsn, "ops_dispatcher")
            try:
                publisher = Publisher(pool, broker)
                self.assertEqual(await publisher.publish_once(task_id=UUID(task)), 1)
                self.assertIsNone(await broker.queue.get(fail=False))
                message = await broker.manual.get(timeout=5)
                delivery = Delivery.model_validate_json(message.body)
                self.assertEqual(str(delivery.task_id), task)
                await message.ack()
                with psycopg.connect(self.worker_dsn) as c:
                    self.assertFalse(
                        c.execute(
                            "SELECT ops.validate_delivery(%s,%s)",
                            (delivery.outbox_id, delivery.task_id),
                        ).fetchone()[0]
                    )
                with psycopg.connect(self.owner) as c:
                    self.assertEqual(
                        c.execute(
                            "SELECT event_type FROM ops.outbox WHERE id=%s", (delivery.outbox_id,)
                        ).fetchone()[0],
                        "task.manual",
                    )
                    c.execute(
                        "UPDATE ops.task_dispatch SET next_recovery_at=now()-interval '1 second' WHERE task_id=%s",
                        (task,),
                    )
                    count = c.execute(
                        "SELECT count(*) FROM ops.outbox WHERE task_id=%s", (task,)
                    ).fetchone()[0]
                await publisher.recover_once()
                with psycopg.connect(self.owner) as c:
                    self.assertEqual(
                        c.execute(
                            "SELECT count(*) FROM ops.outbox WHERE task_id=%s", (task,)
                        ).fetchone()[0],
                        count,
                    )
            finally:
                await pool.close()
                await broker.queue.delete(if_unused=False, if_empty=False)
                await broker.manual.delete(if_unused=False, if_empty=False)
                await broker.exchange.delete(if_unused=False)
                await broker.dead.delete(if_unused=False)
                await broker.close()

        asyncio.run(exercise())
        self.assertEqual(self.state(task), before)
        self.assertEqual(self.run_protocol(task), "succeeded")
