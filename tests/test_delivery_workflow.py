"""Approval/delivery separation, retry, source integrity, and crash recovery."""
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from pilot import archive as archive_writer
from pilot.archive import ArchiveJournal
from pilot.service import APIError, Registry


def fixture(version="1.0.0", name="sample", extra=None, dependencies=None):
    metadata = {"name": name, "version": version, "title": "Delivery fixture",
                "maintainer": "Maintainer", "email": "maintainer@example.invalid",
                "stata": "16.0", "license": "MIT", "notes": "Exercise reviewed delivery.",
                "dependencies": dependencies or []}
    files = {f"{name}.ado": f"*! {version}\nprogram {name}\nend\n".encode(),
             f"{name}.sthlp": b"{smcl}\nHelp for the fixture.\n", **(extra or {})}
    files[f"{name}.pkg"] = ("v 3\nd Delivery fixture\n" + "".join(f"f {path}\n" for path in files)).encode()
    source = io.BytesIO()
    with zipfile.ZipFile(source, "w") as archive:
        for path, data in files.items():
            archive.writestr(path, data)
        archive.writestr("smoke.do", "assert 1 == 1\n")
        archive.writestr("metadata.json", json.dumps(metadata))
        archive.writestr("private-review.txt", "Not an installable file.\n")
    return {"metadata": metadata, "trusted": True, "zip_base64": base64.b64encode(source.getvalue()).decode()}, files


def passing_runner(*args):
    return {"status": "passed", "checks": [], "log": "Delivery unit fixture; Stata is not executed.", "stata_version": "19"}


class DeliveryWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.registry = Registry(self.temp.name, runner=passing_runner)

    def tearDown(self):
        self.registry.close()
        self.temp.cleanup()

    def submit(self, **kwargs):
        body, _ = fixture(**kwargs)
        submission = self.registry.submit(body)
        self.registry.executor.submit(lambda: None).result(timeout=10)
        return self.registry.get("submissions", submission["id"])

    def approve(self, submission):
        # The caller's confirmation/reviewer gates are tested by service tests;
        # these tests isolate the adapter's durable approval/delivery contract.
        return self.registry.approve_candidate(submission, "Reviewed fixture.")

    def publish(self, **kwargs):
        submission = self.approve(self.submit(**kwargs))
        return self.registry.deliver(submission["id"])

    def output(self):
        return {str(path.relative_to(self.registry.current_archive)): path.read_bytes()
                for path in self.registry.current_archive.rglob("*") if path.is_file()}

    def reopen(self):
        self.registry.close()
        self.registry = Registry(self.temp.name, runner=passing_runner)

    def test_approval_is_pending_then_delivery_writes_exact_files_and_receipt(self):
        submission = self.approve(self.submit(extra={"data/raw.bin": b"\x00\xff\r\n"}))
        self.assertEqual("pending", submission["delivery"]["status"])
        self.assertEqual({}, self.output())
        self.assertEqual([], self.registry.rows("releases"))
        self.assertEqual("maintainer@example.invalid", self.registry.setting("owner:sample")["email"])
        delivered = self.registry.deliver(submission["id"])
        receipt = delivered["delivery"]["receipt"]
        self.assertEqual("delivered", delivered["delivery"]["status"])
        self.assertEqual("approved", delivered["status"])
        self.assertFalse(receipt["production_connected"])
        for path in receipt["files"]:
            self.assertEqual(hashlib.sha256(self.output()[path]).hexdigest(), receipt["file_hashes"][path])
        self.assertNotIn("s/smoke.do", self.output())
        self.assertNotIn("s/private-review.txt", self.output())
        self.assertEqual(1, len(self.registry.rows("releases")))

    def test_repeat_delivery_is_idempotent_even_after_a_later_update(self):
        first = self.publish()
        self.publish(version="1.1.0")
        before = self.output()
        again = self.registry.deliver(first["id"])
        self.assertEqual(first["delivery"]["receipt"], again["delivery"]["receipt"])
        self.assertEqual(1, len(again["delivery"]["attempts"]))
        self.assertEqual(before, self.output())
        self.assertEqual(2, len(self.registry.rows("releases")))

    def test_failed_write_rolls_back_then_retry_delivers_and_records_both_attempts(self):
        first = self.publish(extra={"old.dat": b"keep until update succeeds"})
        submission = self.approve(self.submit(version="1.1.0"))
        before = self.output()
        real_replace = archive_writer.replace_file
        tripped = False

        def failing_replace(path, data):
            nonlocal tripped
            if path == self.registry.current_archive / "s/sample.pkg" and not tripped:
                tripped = True
                raise OSError("Simulated disk write failure")
            return real_replace(path, data)

        with patch.object(archive_writer, "replace_file", side_effect=failing_replace):
            result = self.registry.deliver(submission["id"])
        self.assertEqual("failed", result["delivery"]["status"])
        self.assertIn("disk write", result["delivery"]["error"])
        self.assertNotIn("receipt", result["delivery"])
        self.assertEqual(before, self.output())
        self.assertEqual(first["id"], self.registry.setting("current_archive")["sample"]["submission_id"])
        result = self.registry.deliver(submission["id"])
        self.assertEqual(["failed", "delivered"], [attempt["status"] for attempt in result["delivery"]["attempts"]])
        self.assertNotIn("s/old.dat", self.output())
        self.assertEqual(["s/old.dat"], result["delivery"]["receipt"]["removed_files"])

    def test_checked_bytes_and_metadata_cannot_change_after_approval(self):
        submission = self.approve(self.submit())
        path = Path(self.temp.name) / "submissions" / submission["id"] / "source.zip"
        original = path.read_bytes()
        path.write_bytes(b"changed after review")
        result = self.registry.deliver(submission["id"])
        self.assertEqual("failed", result["delivery"]["status"])
        self.assertIn("checksum", result["delivery"]["error"])
        path.write_bytes(original)
        result["metadata"]["title"] = "Metadata changed after review"
        self.registry.save("submissions", result)
        result = self.registry.deliver(submission["id"])
        self.assertEqual("failed", result["delivery"]["status"])
        self.assertIn("metadata changed", result["delivery"]["error"])
        self.assertEqual({}, self.output())

    def test_stale_approved_candidate_cannot_replace_a_newer_delivery(self):
        self.publish()
        one = self.approve(self.submit(version="1.1.0"))
        two = self.approve(self.submit(version="1.2.0"))
        self.registry.deliver(one["id"])
        before = self.output()
        stale = self.registry.deliver(two["id"])
        self.assertEqual("failed", stale["delivery"]["status"])
        self.assertIn("changed since this submission", stale["delivery"]["error"])
        self.assertEqual(before, self.output())
        self.assertEqual(2, len(self.registry.rows("releases")))

    def test_stale_candidate_is_also_blocked_at_approval(self):
        self.publish()
        candidate = self.submit(version="1.2.0")
        self.publish(version="1.1.0")
        with self.assertRaisesRegex(APIError, "changed since this submission"):
            self.approve(candidate)

    def test_dependency_update_after_checks_blocks_delivery_without_changing_main_package(self):
        self.publish(name="support")
        dependencies = [{"name": "support", "version": "1.0.0"}]
        self.publish(dependencies=dependencies)
        candidate = self.approve(self.submit(version="1.1.0", dependencies=dependencies))
        self.publish(name="support", version="1.1.0")
        before = self.output()
        result = self.registry.deliver(candidate["id"])
        self.assertEqual("failed", result["delivery"]["status"])
        self.assertIn("Dependency support changed", result["delivery"]["error"])
        self.assertEqual(before, self.output())
        self.assertEqual("1.0.0", self.registry.setting("current_archive")["sample"]["version"])

    def test_concurrent_approval_and_delivery_reserve_one_package_version(self):
        candidates = [self.submit(), self.submit()]
        barrier = threading.Barrier(2)

        def approve(candidate):
            barrier.wait()
            try:
                return self.approve(candidate)["id"]
            except APIError:
                return None

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(approve, candidates))
        approved = [result for result in results if result]
        self.assertEqual(1, len(approved))
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(self.registry.deliver, [approved[0], approved[0]]))
        self.assertEqual(results[0]["delivery"]["receipt"], results[1]["delivery"]["receipt"])
        self.assertEqual(1, len(self.registry.rows("releases")))

    def interrupted_journal(self, candidate):
        candidate = deepcopy(candidate)
        attempt_id = "a" * 32
        candidate["delivery"] = {"status": "delivering", "attempts": [{"id": attempt_id, "at": "fixture", "status": "delivering"}]}
        self.registry.save("submissions", candidate)
        before = {"s/sample.ado": (self.registry.current_archive / "s/sample.ado").read_bytes(),
                  "s/new.dat": None}
        after = {"s/sample.ado": b"partial write from interrupted candidate", "s/new.dat": b"new"}
        journal = ArchiveJournal(self.registry.data / "delivery-journals" / f"{attempt_id}.json",
                                 {"attempt_id": attempt_id, "submission_id": candidate["id"]})
        journal.prepare(self.registry.current_archive, before, after)
        for relative, data in after.items():
            archive_writer.replace_file(self.registry.current_archive / relative, data)
        return journal

    def test_restart_recovers_partial_write_and_allows_retry(self):
        self.publish()
        candidate = self.approve(self.submit(version="1.1.0"))
        before = self.output()
        journal = self.interrupted_journal(candidate)
        self.reopen()
        self.assertEqual(before, self.output())
        self.assertFalse(journal.path.exists())
        recovered = self.registry.get("submissions", candidate["id"])
        self.assertEqual("failed", recovered["delivery"]["status"])
        self.assertIn("restored", recovered["delivery"]["error"])
        result = self.registry.deliver(candidate["id"])
        self.assertEqual("delivered", result["delivery"]["status"])

    def test_restart_preserves_committed_delivery_when_journal_cleanup_was_interrupted(self):
        candidate = self.approve(self.submit())
        with patch.object(ArchiveJournal, "clear", side_effect=OSError("Interrupted cleanup")):
            result = self.registry.deliver(candidate["id"])
        self.assertEqual("delivered", result["delivery"]["status"])
        self.assertTrue(list((self.registry.data / "delivery-journals").glob("*.json")))
        before = self.output()
        self.reopen()
        self.assertEqual(before, self.output())
        self.assertFalse(list((self.registry.data / "delivery-journals").glob("*.json")))
        self.assertEqual(result["delivery"]["receipt"], self.registry.deliver(candidate["id"])["delivery"]["receipt"])

    def test_recovery_refuses_to_overwrite_unrelated_external_changes(self):
        self.publish()
        candidate = self.approve(self.submit(version="1.1.0"))
        journal = self.interrupted_journal(candidate)
        (self.registry.current_archive / "s/sample.ado").write_bytes(b"external operator change")
        self.reopen()
        self.assertTrue(journal.path.exists())
        self.assertTrue(self.registry.handoff_state()["recovery_errors"])
        result = self.registry.deliver(candidate["id"])
        self.assertEqual("failed", result["delivery"]["status"])
        self.assertEqual(b"external operator change", (self.registry.current_archive / "s/sample.ado").read_bytes())

    def test_handoff_contains_only_reviewed_inventory_and_original_change_manifest(self):
        self.publish(extra={"old.dat": b"old"})
        candidate = self.approve(self.submit(version="1.1.0", extra={"new.dat": b"new"}))
        plan = self.registry.handoff_plan(candidate["id"])
        self.assertTrue(plan["ready"])
        self.assertEqual(["s/new.dat"], plan["added"])
        self.assertEqual(["s/old.dat"], plan["removed"])
        self.registry.deliver(candidate["id"])
        with zipfile.ZipFile(io.BytesIO(self.registry.handoff_bundle(candidate["id"]))) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertNotIn("s/private-review.txt", archive.namelist())
            self.assertNotIn("s/smoke.do", archive.namelist())
            self.assertNotIn("s/stata.toc", archive.namelist())
            self.assertEqual(["s/old.dat"], manifest["removed"])
            self.assertEqual(candidate["fingerprint"], manifest["reviewed_fingerprint"])
            for item in manifest["files"]:
                self.assertEqual(item["sha256"], hashlib.sha256(archive.read(item["path"])).hexdigest())
            self.assertFalse(manifest["production_connected"])


if __name__ == "__main__":
    unittest.main()
