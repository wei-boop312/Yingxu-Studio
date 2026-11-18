import json

import psycopg
from test_offline_pipeline import PipelineFixture


class EventTests(PipelineFixture):
    def event_ids(self, text):
        return [int(line[4:]) for line in text.splitlines() if line.startswith("id: ")]

    def test_last_event_cursor_replays_only_later_persisted_events(self):
        task = self.submit()
        self.assertEqual(self.run_worker(task), "succeeded")
        response = self.client.get("/api/v1/tasks/" + task + "/events")
        ids = self.event_ids(response.text)
        self.assertGreater(len(ids), 3)
        middle = ids[len(ids) // 2]
        with self.assertLogs("uvicorn.error", level="INFO") as logged:
            resumed = self.client.get(
                "/api/v1/tasks/" + task + "/events", headers={"last-event-id": str(middle)}
            )
        trace = json.loads(logged.records[-1].getMessage())
        self.assertEqual(
            (trace["task_id"], trace["cursor"], trace["cursor_source"]),
            (task, str(middle), "last-event-id"),
        )
        self.assertEqual(set(trace), {"event", "task_id", "cursor", "cursor_source", "request_id"})
        self.assertEqual(self.event_ids(resumed.text), [i for i in ids if i > middle])
        self.assertIn("succeeded", resumed.text)
        restored = self.client.get(
            "/api/v1/tasks/" + task + "/events", params={"cursor": str(middle)}
        )
        self.assertEqual(self.event_ids(restored.text), [i for i in ids if i > middle])
        reconnected = self.client.get(
            "/api/v1/tasks/" + task + "/events",
            params={"cursor": str(ids[0])},
            headers={"last-event-id": str(middle)},
        )
        self.assertEqual(self.event_ids(reconnected.text), [i for i in ids if i > middle])

    def test_retention_and_empty_stream_reset_cursor(self):
        task = self.submit()
        self.run_worker(task)
        ids = self.event_ids(self.client.get("/api/v1/tasks/" + task + "/events").text)
        with psycopg.connect(self.owner) as c:
            c.execute(
                "DELETE FROM app.task_events WHERE task_id=%s AND id<=%s",
                (task, ids[len(ids) // 2]),
            )
        response = self.client.get(
            "/api/v1/tasks/" + task + "/events", headers={"last-event-id": str(ids[0])}
        )
        self.assertIn("event: stream.reset", response.text)
        with psycopg.connect(self.owner) as c:
            c.execute("DELETE FROM app.task_events WHERE task_id=%s", (task,))
        response = self.client.get(
            "/api/v1/tasks/" + task + "/events", headers={"last-event-id": str(ids[-1])}
        )
        self.assertIn("event: stream.reset", response.text)

    def test_invalid_cursor_and_unknown_task_are_rejected(self):
        from uuid import uuid4

        task = self.submit()
        self.run_worker(task)
        self.assertEqual(
            self.client.get(
                "/api/v1/tasks/" + task + "/events", headers={"last-event-id": "-1"}
            ).status_code,
            422,
        )
        for cursor in ("-1", "9223372036854775808", "bad"):
            self.assertEqual(
                self.client.get(
                    "/api/v1/tasks/" + task + "/events", params={"cursor": cursor}
                ).status_code,
                422,
            )
        self.assertEqual(
            self.client.get(
                "/api/v1/tasks/" + task + "/events",
                params={"cursor": "0"},
                headers={"last-event-id": "bad"},
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.get("/api/v1/tasks/" + str(uuid4()) + "/events").status_code, 404
        )
        self.assertEqual(
            self.client.get("/api/v1/tasks/" + task + "/input").json()["source_asset_version_id"],
            self.version,
        )
        self.assertNotIn("_execution", self.client.get("/api/v1/tasks/" + task + "/input").json())
