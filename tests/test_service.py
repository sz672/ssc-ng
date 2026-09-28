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
        return self.registry.review(submission["id"], {"decision": "approve", "note": "Reviewed test fixture"})

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
        self.registry.review(submission["id"], {"decision": "request_changes", "note": "Explain results"})
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

    def test_dependency_pin_diff_restore_and_capture(self):
        self.approve(self.submit())
        failed = self.submit("sscng-example-1.1.0")
        self.assertEqual("failed", failed["status"])
        self.approve(self.submit("sscng-helper-0.1.0"))
        update = self.submit("sscng-example-1.1.0", parent_id=failed["id"])
        self.assertEqual("passed", update["status"])
        self.assertEqual("0.1.0", update["dependency_releases"][0]["version"])
        self.approve(update)
        diff = self.registry.diff("sscng_example", "1.1.0")
        self.assertEqual("1.0.0", diff["previous_version"])
        self.assertIn("sscng_example.ado", [c["path"] for c in diff["changed"]])
        restored = self.registry.restore("sscng_example", "1.0.0", {"trusted": True})
        self.assertEqual("1.0.0", restored["environment"]["version"])
        snapshot = self.registry.snapshot()
        self.assertEqual(2, len(snapshot["packages"]))
        self.assertEqual("1.1.0", next(p for p in snapshot["packages"] if p["name"] == "sscng_example")["version"])
        self.registry.daily_snapshot()
        self.assertEqual(1, len(self.registry.rows("snapshots")))

    def test_tampered_source_cannot_publish_or_install(self):
        submission = self.submit()
        path = Path(self.temp.name) / "submissions" / submission["id"] / "source.zip"
        path.write_bytes(b"tampered")
        with self.assertRaisesRegex(APIError, "checksum"):
            self.approve(submission)

    def test_transfer_evidence_is_persisted_without_rewriting_release(self):
        self.approve(self.submit())
        before = self.registry.release("sscng_example", "1.0.0")
        transfer = self.registry.transfer({"name": "sscng_example", "to_maintainer": "New example maintainer", "to_email": "new@example.invalid", "evidence": "Operator has reviewed example consent."})
        self.registry.approve_transfer(transfer["id"])
        self.assertEqual("New example maintainer", self.registry.setting("owner:sscng_example")["maintainer"])
        self.assertEqual(before, self.registry.release("sscng_example", "1.0.0"))
        with self.assertRaises(APIError):
            self.registry.approve_transfer(transfer["id"])


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
        status, _ = self.request("POST", "/api/snapshots", {}, {"Content-Type": "application/json", "Origin": "https://untrusted.example"})
        self.assertEqual(403, status)
        status, _ = self.request("GET", "/api/state", headers={"Host": "untrusted.example"})
        self.assertEqual(403, status)
        status, _ = self.request("GET", "/.sscng/registry.sqlite3")
        self.assertEqual(404, status)
        status, _ = self.request("POST", "/api/snapshots", {}, {"Content-Type": "text/plain"})
        self.assertEqual(415, status)

    def test_download_and_stata_package_endpoint_match_archive(self):
        submission = self.submit()
        self.approve(submission)
        status, data = self.request("GET", "/api/releases/sscng_example/1.0.0/download")
        self.assertEqual(200, status)
        self.assertEqual(submission["sha256"], hashlib.sha256(data).hexdigest())
        status, data = self.request("GET", "/packages/sscng_example/1.0.0/sscng_example.pkg")
        self.assertEqual(200, status)
        self.assertIn(b"f sscng_example.ado", data)
        status, data = self.request("GET", "/packages/sscng_example/1.0.0/stata.toc")
        self.assertEqual(200, status)
        self.assertIn(b"p sscng_example", data)


if __name__ == "__main__":
    unittest.main()
