"""Regression coverage for catalog ordering, input errors, and dependency graphs."""

import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import tempfile
import threading
import unittest
import uuid

from pilot.service import APIError, Registry


def passing_runner(*args):
    return {"status": "passed", "checks": [], "log": "Service-test stub; no Stata execution.",
            "stata_version": "19.5"}


class ServiceEdgesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.registry = Registry(self.temp.name, runner=passing_runner)

    def tearDown(self):
        self.registry.close()
        self.temp.cleanup()

    def body(self, example_id="sscng-example-1.0.0"):
        example = next(item for item in self.registry.examples if item["id"] == example_id)
        return {"trusted": True, "metadata": deepcopy(example["metadata"]),
                "zip_base64": base64.b64encode(example["path"].read_bytes()).decode()}

    def submit(self, example_id="sscng-example-1.0.0"):
        submission = self.registry.submit(self.body(example_id))
        self.registry.executor.submit(lambda: None).result(timeout=10)
        return self.registry.get("submissions", submission["id"])

    def approve(self, submission):
        return self.registry.review(submission["id"], {"decision": "approve"})

    def catalog_release(self, name, version, dependencies=()):
        """Seed dependency-only catalog fixtures, including otherwise unreachable cycles."""
        record = {"id": uuid.uuid4().hex, "name": name, "version": version,
                  "sha256": "0" * 64,
                  "metadata": {"name": name, "version": version,
                               "dependencies": [{"name": n, "version": v} for n, v in dependencies]}}
        with self.registry.connect() as db:
            db.execute("INSERT INTO releases(id,name,version,value) VALUES(?,?,?,?)",
                       (record["id"], name, version, json.dumps(record)))

    def test_old_release_published_later_does_not_replace_latest_or_reverse_diff(self):
        self.approve(self.submit("sscng-helper-0.1.0"))
        self.approve(self.submit("sscng-example-1.1.0"))
        self.approve(self.submit("sscng-example-1.0.0"))
        snapshot = self.registry.snapshot()
        current = next(item for item in snapshot["packages"] if item["name"] == "sscng_example")
        self.assertEqual("1.1.0", current["version"])
        self.assertIsNone(self.registry.diff("sscng_example", "1.0.0")["previous_version"])
        newer_diff = self.registry.diff("sscng_example", "1.1.0")
        self.assertEqual("1.0.0", newer_diff["previous_version"])
        self.assertIn("sscng_example.ado", [item["path"] for item in newer_diff["changed"]])

    def test_catalog_uses_numeric_version_order(self):
        self.catalog_release("example", "1.10.0")
        self.catalog_release("example", "1.2.0")
        snapshot = self.registry.snapshot()
        self.assertEqual("1.10.0", snapshot["packages"][0]["version"])

    def test_malformed_optional_submission_ids_return_validation_errors(self):
        for key in ("parent_id", "source_submission_id"):
            for invalid in ({"bad": "id"}, ["bad"], 0, False, "", "not-an-id", "a" * 31, "G" * 32):
                with self.subTest(field=key, value=invalid):
                    body = self.body()
                    body[key] = invalid
                    with self.assertRaises(APIError) as error:
                        self.registry.submit(body)
                    self.assertEqual(400, error.exception.status)
                    self.assertIn(key, str(error.exception))
        self.assertEqual([], self.registry.rows("submissions"))

    def test_dependency_cycles_and_conflicting_pins_are_rejected(self):
        self.catalog_release("cycle_a", "1.0.0", [("cycle_b", "1.0.0")])
        self.catalog_release("cycle_b", "1.0.0", [("cycle_a", "1.0.0")])
        with self.assertRaisesRegex(APIError, "cycle"):
            self.registry.dependencies({"name": "candidate", "dependencies": [{"name": "cycle_a", "version": "1.0.0"}]})

        self.catalog_release("shared", "1.0.0")
        self.catalog_release("shared", "2.0.0")
        self.catalog_release("left", "1.0.0", [("shared", "1.0.0")])
        self.catalog_release("right", "1.0.0", [("shared", "2.0.0")])
        with self.assertRaisesRegex(APIError, "Conflicting pinned versions"):
            self.registry.dependencies({"name": "candidate", "dependencies": [
                {"name": "left", "version": "1.0.0"}, {"name": "right", "version": "1.0.0"}]})

    def test_shared_transitive_dependency_is_installed_once_before_dependants(self):
        self.catalog_release("shared", "1.0.0")
        self.catalog_release("left", "1.0.0", [("shared", "1.0.0")])
        self.catalog_release("right", "1.0.0", [("shared", "1.0.0")])
        resolved = self.registry.dependencies({"name": "candidate", "dependencies": [
            {"name": "left", "version": "1.0.0"}, {"name": "right", "version": "1.0.0"}]})
        names = [item["name"] for item in resolved]
        self.assertEqual(1, names.count("shared"))
        self.assertEqual({"shared", "left", "right"}, set(names))
        self.assertLess(names.index("shared"), names.index("left"))
        self.assertLess(names.index("shared"), names.index("right"))

    def test_concurrent_approvals_publish_only_one_bundle_per_version(self):
        candidates = [self.submit(), self.submit()]
        barrier = threading.Barrier(2)

        def approve_together(candidate):
            barrier.wait(timeout=10)
            try:
                return self.approve(candidate)["status"]
            except APIError as error:
                return error.status

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(approve_together, candidates))
        self.assertCountEqual(["approved", 409], results)
        self.assertEqual(1, len(self.registry.rows("releases")))
        self.assertEqual(1, sum(item["status"] == "approved" for item in self.registry.rows("submissions")))


if __name__ == "__main__":
    unittest.main()
