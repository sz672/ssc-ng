"""Author confirmation, candidate identity, revisions, and API contracts."""
from http.client import HTTPConnection
import json
import tempfile
import threading
import unittest

from pilot.service import APIError, Registry, make_server
from test_current_archive import package_body, passing_runner


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.registry = Registry(self.temp.name, runner=passing_runner)

    def tearDown(self):
        self.registry.close()
        self.temp.cleanup()

    def submit(self, body=None, **kwargs):
        body = body or package_body(**kwargs)[0]
        item = self.registry.submit(body)
        self.registry.executor.submit(lambda: None).result(timeout=10)
        return self.registry.get("submissions", item["id"])

    def code(self, item):
        return next(m["code"] for m in self.registry.demo_mailbox()["messages"] if m["submission_id"] == item["id"])

    def confirm(self, item):
        return self.registry.confirm(item["id"], {"code": self.code(item)})

    def approve(self, item):
        return self.registry.review(item["id"], {"decision": "approve", "reviewer": "Reviewer A", "note": "Reviewed files and executable example."})

    def deliver(self, item):
        self.confirm(item)
        self.approve(item)
        result = self.registry.deliver(item["id"])
        self.assertEqual("delivered", result["delivery"]["status"], result["delivery"].get("error"))
        return result

    def test_confirmation_and_reviewer_gate_approval_and_confirmation_is_one_use(self):
        item = self.submit()
        with self.assertRaisesRegex(APIError, "Confirm the maintainer"):
            self.approve(item)
        confirmed = self.confirm(item)
        self.assertEqual("verified", confirmed["confirmation"]["status"])
        with self.assertRaisesRegex(APIError, "already been used"):
            self.confirm(item)
        with self.assertRaisesRegex(APIError, "reviewer's name"):
            self.registry.review(item["id"], {"decision": "approve"})
        approved = self.approve(item)
        self.assertEqual("Reviewer A", approved["reviewer"])
        self.assertEqual("pending", approved["delivery"]["status"])
        self.assertEqual([], self.registry.archive_state()["packages"])

    def test_wrong_expired_and_replaced_codes_cannot_confirm(self):
        item = self.submit()
        original = self.code(item)
        self.registry.request_confirmation(item["id"])
        with self.assertRaises(APIError):
            self.registry.confirm(item["id"], {"code": original})
        for _ in range(4):
            with self.assertRaises(APIError):
                self.registry.confirm(item["id"], {"code": "not-the-code"})
        self.assertEqual("locked", self.registry.get("submissions", item["id"])["confirmation"]["status"])
        with self.assertRaisesRegex(APIError, "locked"):
            self.confirm(item)
        self.registry.request_confirmation(item["id"])
        challenge = self.registry.setting(f"confirmation:{item['id']}")
        challenge["expires_at"] = "2000-01-01T00:00:00+00:00"
        self.registry.set_setting(f"confirmation:{item['id']}", challenge)
        with self.assertRaisesRegex(APIError, "expired"):
            self.confirm(item)

    def test_confirmation_during_checks_is_not_lost_by_worker(self):
        entered, finish = threading.Event(), threading.Event()
        def slow_runner(*args):
            entered.set()
            finish.wait(timeout=5)
            return passing_runner(*args)
        self.registry.runner = slow_runner
        item = self.registry.submit(package_body()[0])
        self.assertTrue(entered.wait(timeout=5))
        try:
            self.confirm(item)
        finally:
            finish.set()
        self.registry.executor.submit(lambda: None).result(timeout=10)
        saved = self.registry.get("submissions", item["id"])
        self.assertEqual("passed", saved["status"])
        self.assertEqual("verified", saved["confirmation"]["status"])
        self.assertEqual(saved["fingerprint"], saved["checked_fingerprint"])

    def test_updates_must_use_recorded_owner_even_before_first_delivery(self):
        item = self.submit()
        self.confirm(item)
        self.approve(item)
        body, _ = package_body(version="1.1.0")
        body["metadata"]["email"] = "someoneelse@example.invalid"
        with self.assertRaisesRegex(APIError, "recorded maintainer"):
            self.submit(body)
        body["metadata"]["email"] = item["metadata"]["email"]
        update = self.submit(body)
        self.assertEqual("update", update["kind"])
        self.assertEqual(item["metadata"]["email"], update["confirmation"]["email"])

    def test_confirmation_is_bound_to_exact_revision_and_old_revision_cannot_approve(self):
        item = self.submit()
        self.confirm(item)
        self.registry.review(item["id"], {"decision": "request_changes", "reviewer": "Reviewer A", "note": "Clarify the example."})
        body = {"trusted": True, "metadata": {**item["metadata"], "notes": "Clarified example."},
                "parent_id": item["id"], "source_submission_id": item["id"]}
        revision = self.submit(body)
        with self.assertRaises(APIError):
            self.registry.confirm(revision["id"], {"code": self.code(item)})
        self.assertEqual("pending", revision["confirmation"]["status"])
        with self.assertRaises(APIError):
            self.approve(item)
        comparison = self.registry.comparison(revision["id"])
        self.assertEqual("Clarify the example.", comparison["parent"]["review_note"])
        self.assertEqual("Reviewer A", comparison["parent"]["reviewer"])
        self.confirm(revision)
        self.approve(revision)

    def test_changed_checked_metadata_cannot_be_approved(self):
        item = self.submit()
        self.confirm(item)
        changed = self.registry.get("submissions", item["id"])
        changed["metadata"]["notes"] = "Changed after checking"
        self.registry.save("submissions", changed)
        with self.assertRaisesRegex(APIError, "checked revision"):
            self.approve(item)

    def test_comparison_reports_files_metadata_and_stale_base(self):
        first = self.submit(extra_files={"old.dat": b"old"}, install_extra=["old.dat"])
        self.deliver(first)
        self.assertFalse(self.registry.comparison(first["id"])["stale"])
        update = self.submit(version="1.1.0", extra_files={"new.dat": b"new"}, install_extra=["new.dat"])
        comparison = self.registry.comparison(update["id"])
        self.assertFalse(comparison["stale"])
        self.assertEqual("1.0.0", comparison["base"]["version"])
        self.assertIn("new.dat", comparison["files"]["added"])
        self.assertIn("old.dat", comparison["files"]["removed"])
        self.assertIn("sample.ado", [c["path"] for c in comparison["files"]["changed"]])
        self.assertIn("version", [c["field"] for c in comparison["metadata"]])
        other = self.submit(version="1.2.0")
        self.deliver(other)
        self.assertTrue(self.registry.comparison(update["id"])["stale"])
        self.confirm(update)
        with self.assertRaisesRegex(APIError, "archive changed"):
            self.approve(update)

    def test_approved_undelivered_revision_can_be_superseded_and_must_be_rechecked(self):
        item = self.submit()
        self.confirm(item)
        self.approve(item)
        revision = self.submit({"trusted": True, "metadata": {**item["metadata"], "notes": "Corrected before delivery"},
                                "parent_id": item["id"], "source_submission_id": item["id"]})
        self.assertEqual("superseded", self.registry.get("submissions", item["id"])["status"])
        with self.assertRaises(APIError):
            self.registry.deliver(item["id"])
        self.assertEqual("pending", revision["confirmation"]["status"])
        self.deliver(revision)


class WorkflowHTTPTest(unittest.TestCase):
    def setUp(self):
        WorkflowTest.setUp(self)
        self.server = make_server(self.registry, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        WorkflowTest.tearDown(self)

    def request(self, method, path, body=None):
        client = HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        client.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
        result = client.getresponse()
        status, data = result.status, result.read()
        client.close()
        return status, data

    def test_intake_confirmation_delivery_and_receipt_api(self):
        body, _ = package_body()
        status, raw = self.request("POST", "/api/intake/inspect", {"zip_base64": body["zip_base64"]})
        self.assertEqual(200, status)
        self.assertEqual("sample", json.loads(raw)["metadata"]["name"])
        self.assertEqual([], self.registry.rows("submissions"))
        status, raw = self.request("POST", "/api/submissions", body)
        self.assertEqual(201, status)
        item = json.loads(raw)["submission"]
        self.registry.executor.submit(lambda: None).result(timeout=10)
        status, raw = self.request("GET", "/api/demo-mailbox")
        self.assertEqual(200, status)
        code = json.loads(raw)["messages"][0]["code"]
        route = f"/api/submissions/{item['id']}"
        self.assertEqual(200, self.request("POST", route + "/confirm", {"code": code})[0])
        self.assertEqual(200, self.request("POST", route + "/review", {"decision": "approve", "reviewer": "HTTP reviewer"})[0])
        self.assertEqual(409, self.request("GET", route + "/receipt")[0])
        self.assertEqual(200, self.request("GET", route + "/handoff")[0])
        self.assertEqual(200, self.request("GET", route + "/reproduce")[0])
        status, raw = self.request("POST", route + "/deliver", {})
        self.assertEqual("delivered", json.loads(raw)["submission"]["delivery"]["status"])
        status, raw = self.request("GET", route + "/receipt")
        self.assertEqual(200, status)
        self.assertFalse(json.loads(raw)["production_connected"])


if __name__ == "__main__":
    unittest.main()
