"""Persistence, review gates, archive fidelity, and HTTP boundary tests."""
import base64
from copy import deepcopy
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest

from pilot.service import APIError, Registry, make_server


def passing_runner(zip_bytes, metadata, dependencies, work_dir, stata_path):
    return {"status": "passed", "checks": [{"name": "Test runner", "status": "passed", "detail": "Stub used only for service unit tests."}], "log": "test fixture", "stata_version": "19", "library_path": str(work_dir / "plus")}


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.registry = Registry(self.temp.name, runner=passing_runner)

    def tearDown(self):
        self.registry.close()
        self.temp.cleanup()

    def submit(self, identifier="sscng-example-1.0.0", **extra):
        example = next(e for e in self.registry.examples if e["id"] == identifier)
        body = {"metadata": deepcopy(example["metadata"]), "trusted": True, "zip_base64": base64.b64encode(example["path"].read_bytes()).decode(), **extra}
        submission = self.registry.submit(body)
        self.registry.executor.submit(lambda: None).result(timeout=10)
        return self.registry.get("submissions", submission["id"])

    def approve(self, submission):
        record = self.registry.get("submissions", submission["id"])
        if record.get("confirmation", {}).get("status") != "verified":
            message = next(m for m in self.registry.demo_mailbox()["messages"] if m["submission_id"] == submission["id"])
            self.registry.confirm(submission["id"], {"code": message["code"]})
        self.registry.review(submission["id"], {"decision": "approve", "reviewer": "Test reviewer", "note": "Reviewed test fixture"})
        result = self.registry.deliver(submission["id"])
        self.assertEqual("delivered", result["delivery"]["status"], result["delivery"].get("error"))
        return result

    def test_trust_is_required_and_approval_requires_completed_checks(self):
        with self.assertRaisesRegex(APIError, "trust"):
            self.submit(trusted=False)
        submission = self.submit("sscng-example-1.1.0")
        self.assertEqual("failed", submission["status"])
        self.assertIn("dependency resolution", submission["log"])
        with self.assertRaisesRegex(APIError, "passing checks"):
            self.approve(submission)

    def test_revision_gate_immutable_release_and_restart(self):
        submission = self.submit()
        self.assertEqual("passed", submission["status"])
        self.registry.review(submission["id"], {"decision": "request_changes", "reviewer": "Test reviewer", "note": "Explain results"})
        with self.assertRaises(APIError):
            self.approve(submission)
        body = {"metadata": submission["metadata"], "source_submission_id": submission["id"], "parent_id": submission["id"], "trusted": True}
        with self.assertRaisesRegex(APIError, "Change the package"):
            self.registry.submit(body)
        body["metadata"] = {**body["metadata"], "notes": "Updated explanation"}
        revision = self.registry.submit(body)
        self.registry.executor.submit(lambda: None).result(timeout=10)
        self.approve(revision)
        release = self.registry.release("sscng_example", "1.0.0")
        self.assertEqual(submission["sha256"], release["sha256"])
        with self.assertRaisesRegex(APIError, "already published"):
            self.submit()
        self.registry.close()
        self.registry = Registry(self.temp.name, runner=passing_runner)
        self.assertEqual("approved", self.registry.get("submissions", revision["id"])["status"])
        self.assertEqual(1, len(self.registry.rows("releases")))

    def test_dependency_pin_and_current_candidate_comparison(self):
        self.approve(self.submit())
        failed = self.submit("sscng-example-1.1.0")
        self.assertEqual("failed", failed["status"])
        self.approve(self.submit("sscng-helper-0.1.0"))
        update = self.submit("sscng-example-1.1.0", parent_id=failed["id"])
        self.assertEqual("passed", update["status"])
        self.assertEqual("0.1.0", update["dependency_releases"][0]["version"])
        comparison = self.registry.comparison(update["id"])
        self.assertEqual("1.0.0", comparison["base"]["version"])
        self.assertIn("sscng_example.ado", [change["path"] for change in comparison["files"]["changed"]])
        self.approve(update)
        current = next(item for item in self.registry.archive_state()["packages"] if item["name"] == "sscng_example")
        self.assertEqual("1.1.0", current["version"])
        self.assertEqual(self.registry.release("sscng_helper", "0.1.0")["sha256"], update["dependency_releases"][0]["sha256"])

    def test_tampered_source_cannot_be_approved(self):
        submission = self.submit()
        path = Path(self.temp.name) / "submissions" / submission["id"] / "source.zip"
        path.write_bytes(b"tampered")
        with self.assertRaisesRegex(APIError, "checksum"):
            self.approve(submission)

    def test_restart_preserves_existing_legacy_records_and_recorded_owner(self):
        self.approve(self.submit())
        before = self.registry.release("sscng_example", "1.0.0")
        # Model a database created by the earlier storage prototype. Retiring
        # those features must not migrate away existing records or ownership.
        with self.registry.connect() as db:
            db.execute("CREATE TABLE snapshots(id TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE transfers(id TEXT PRIMARY KEY, value TEXT NOT NULL)")
        snapshot = {"id": "old-snapshot", "packages": [{"name": "sscng_example", "version": "1.0.0"}]}
        transfer = {"id": "old-transfer", "name": "sscng_example", "status": "approved", "evidence": "Previously recorded consent."}
        self.registry.save("snapshots", snapshot)
        self.registry.save("transfers", transfer)
        owner = {"maintainer": "Recorded maintainer", "email": "recorded@example.invalid"}
        self.registry.set_setting("owner:sscng_example", owner)
        self.registry.close()
        self.registry = Registry(self.temp.name, runner=passing_runner)
        self.assertEqual(snapshot, self.registry.get("snapshots", snapshot["id"]))
        self.assertEqual(transfer, self.registry.get("transfers", transfer["id"]))
        self.assertEqual({"name": "sscng_example", **owner}, self.registry.owner_for("sscng_example"))
        self.assertEqual(before, self.registry.release("sscng_example", "1.0.0"))


class HTTPTest(unittest.TestCase):
    submit = RegistryTest.submit
    approve = RegistryTest.approve

    def setUp(self):
        RegistryTest.setUp(self)
        self.server = make_server(self.registry, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        RegistryTest.tearDown(self)

    def request(self, method, path, body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers or {})
        response = connection.getresponse()
        status, data = response.status, response.read()
        connection.close()
        return status, data

    def test_http_origin_host_and_private_storage(self):
        status, data = self.request("GET", "/api/state")
        self.assertEqual(200, status)
        state = json.loads(data)
        self.assertEqual(1, state["api_revision"])
        self.assertIn("archive", state)
        self.assertIn("handoff", state)
        status, _ = self.request("POST", "/api/submissions", {}, {"Content-Type": "application/json", "Origin": "https://untrusted.example"})
        self.assertEqual(403, status)
        status, _ = self.request("GET", "/api/state", headers={"Host": "untrusted.example"})
        self.assertEqual(403, status)
        status, _ = self.request("GET", "/.sscng/registry.sqlite3")
        self.assertEqual(404, status)
        status, _ = self.request("POST", "/api/submissions", {}, {"Content-Type": "text/plain"})
        self.assertEqual(415, status)

    def test_submission_download_and_current_stata_endpoint_match_reviewed_files(self):
        submission = self.submit()
        self.approve(submission)
        status, data = self.request("GET", f"/api/submissions/{submission['id']}/download")
        self.assertEqual(200, status)
        self.assertEqual(submission["sha256"], hashlib.sha256(data).hexdigest())
        status, data = self.request("GET", "/archive/s/sscng_example.pkg")
        self.assertEqual(200, status)
        self.assertIn(b"f sscng_example.ado", data)
        status, data = self.request("GET", "/archive/s/stata.toc")
        self.assertEqual(200, status)
        self.assertIn(b"p sscng_example", data)

    def test_retired_storage_routes_do_not_mutate_saved_submission_state(self):
        submission = self.approve(self.submit())
        before = self.registry.state("")
        requests = [
            ("POST", "/api/snapshots"),
            ("POST", "/api/transfers"),
            ("POST", "/api/transfers/" + "a" * 32 + "/approve"),
            ("POST", "/api/releases/sscng_example/1.0.0/restore"),
            ("GET", "/api/releases/sscng_example/1.0.0/download"),
            ("GET", "/api/releases/sscng_example/1.0.0/diff"),
            ("GET", "/packages/sscng_example/1.0.0/sscng_example.pkg"),
            ("GET", "/packages/sscng_example/1.0.0/stata.toc"),
        ]
        for method, path in requests:
            with self.subTest(method=method, path=path):
                status, _ = self.request(method, path, {"trusted": True} if method == "POST" else None,
                                         {"Content-Type": "application/json"})
                self.assertEqual(404, status)
        self.assertEqual(before, self.registry.state(""))
        self.assertEqual(submission["delivery"]["receipt"], self.registry.get("submissions", submission["id"])["delivery"]["receipt"])
        self.assertFalse((self.registry.data / "environment").exists())


if __name__ == "__main__":
    unittest.main()
