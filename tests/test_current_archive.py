"""Submission handoff: today's installable files, approval gates, and HTTP output."""

import base64
from copy import deepcopy
from http.client import HTTPConnection
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from pilot import archive as archive_writer

from pilot.service import APIError, Registry, make_server


def passing_runner(zip_bytes, metadata, dependencies, work_dir, stata_path):
    return {"status": "passed", "checks": [], "log": "Service-test stub; no Stata execution.",
            "stata_version": "19.5"}


def package_body(name="sample", version="1.0.0", extra_files=None, install_extra=()):
    """Build independently specified content; do not reuse the export implementation."""
    metadata = {"name": name, "version": version, "title": "Archive handoff fixture",
                "maintainer": "Test maintainer", "email": "test@example.invalid",
                "stata": "16.0", "license": "MIT", "dependencies": [],
                "notes": "Fixture for current archive publication."}
    files = {f"{name}.ado": f"*! {name} {version}\nprogram {name}\nend\n".encode(),
             f"{name}.sthlp": b"{smcl}\r\nFixture help.\r\n",
             "smoke.do": b"assert 1 == 1\n",
             "metadata.json": json.dumps(metadata).encode(),
             "private-review.txt": b"Not part of the installable inventory.\n"}
    files.update(extra_files or {})
    installed = [f"{name}.ado", f"{name}.sthlp", *install_extra]
    # Preserve a BOM and CRLF to detect transformations of the accepted files.
    files[f"{name}.pkg"] = ("\ufeffv 3\r\nd Archive handoff fixture\r\n" +
                              "".join(f"f {path}\r\n" for path in installed)).encode()
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    body = {"trusted": True, "metadata": metadata,
            "zip_base64": base64.b64encode(memory.getvalue()).decode()}
    published = {path: files[path] for path in [f"{name}.pkg", *installed]}
    return body, published


class ArchiveFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.registry = Registry(self.temp.name, runner=passing_runner)

    def tearDown(self):
        self.registry.close()
        self.temp.cleanup()

    def submit(self, body=None, **package_options):
        if body is None:
            body, _ = package_body(**package_options)
        candidate = self.registry.submit(body)
        self.registry.executor.submit(lambda: None).result(timeout=10)
        return self.registry.get("submissions", candidate["id"])

    def approve(self, candidate):
        latest = self.registry.get("submissions", candidate["id"])
        if latest.get("confirmation", {}).get("status") != "verified":
            message = next(item for item in self.registry.demo_mailbox()["messages"]
                           if item["submission_id"] == candidate["id"])
            self.registry.confirm(candidate["id"], {"code": message["code"]})
        return self.registry.review(candidate["id"], {"decision": "approve", "reviewer": "Test reviewer",
                                                       "note": "Checked fixture."})

    def publish(self, candidate):
        self.approve(candidate)
        result = self.registry.deliver(candidate["id"])
        self.assertEqual("delivered", result["delivery"]["status"], result["delivery"])
        return result

    def output(self):
        root = self.registry.current_archive
        return {str(path.relative_to(root)): path.read_bytes()
                for path in root.rglob("*") if path.is_file()}

    def current(self, name="sample"):
        return next(item for item in self.registry.archive_state()["packages"] if item["name"] == name)


class CurrentArchiveTest(ArchiveFixture, unittest.TestCase):
    def test_passing_candidate_is_private_until_approved_and_delivered(self):
        body, expected = package_body(extra_files={"data/example.bin": b"\x00\xff\r\n\x80"},
                                      install_extra=["data/example.bin"])
        candidate = self.submit(body)
        self.assertEqual("passed", candidate["status"])
        self.assertEqual([], self.registry.archive_state()["packages"])
        self.assertEqual({}, self.output())
        approved = self.approve(candidate)
        self.assertEqual("approved", approved["status"])
        self.assertEqual("pending", approved["delivery"]["status"])
        self.assertEqual({}, self.output())
        self.assertEqual([], self.registry.archive_state()["packages"])
        delivered = self.registry.deliver(candidate["id"])
        self.assertEqual("delivered", delivered["delivery"]["status"])
        actual = self.output()
        for path, data in expected.items():
            self.assertEqual(data, actual[f"s/{path}"])
        self.assertNotIn("s/smoke.do", actual)
        self.assertNotIn("s/metadata.json", actual)
        self.assertNotIn("s/private-review.txt", actual)
        item = self.current()
        self.assertEqual("1.0.0", item["version"])
        self.assertEqual(candidate["id"], item["submission_id"])
        self.assertEqual(body["metadata"], item["metadata"])
        self.assertTrue(item["published_at"])
        self.assertCountEqual([f"s/{path}" for path in expected], item["files"])
        state = self.registry.archive_state("http://127.0.0.1:8765")
        self.assertEqual("local", state["mode"])
        self.assertEqual((Path(self.temp.name) / "current-archive").resolve(), Path(state["root"]))
        self.assertEqual("http://127.0.0.1:8765/archive/s/", state["packages"][0]["install_url"])
        self.assertTrue(state["packages"][0]["download_url"].endswith("/api/archive/sample/download"))

    def test_reserved_stata_index_path_is_rejected_without_partial_publication(self):
        body, _ = package_body(extra_files={"stata.toc/payload.ado": b"program payload\nend\n"},
                               install_extra=["stata.toc/payload.ado"])
        candidate = self.submit(body)
        self.assertEqual("failed", candidate["status"])
        self.assertTrue(any("stata.toc" in check["detail"] for check in candidate["checks"]))
        with self.assertRaises(APIError):
            self.approve(candidate)
        self.assertEqual({}, self.output())
        self.assertEqual([], self.registry.archive_state()["packages"])
        self.assertEqual("failed", self.registry.get("submissions", candidate["id"])["status"])
        self.assertEqual([], self.registry.rows("releases"))

    def test_explicitly_listed_smoke_and_metadata_are_installable(self):
        body, expected = package_body(install_extra=["smoke.do", "metadata.json"])
        self.publish(self.submit(body))
        for path, data in expected.items():
            self.assertEqual(data, self.output()[f"s/{path}"])

    def test_update_removes_obsolete_owned_files_and_preserves_other_package(self):
        first, _ = package_body(extra_files={"old/example.dat": b"obsolete\n"},
                                install_extra=["old/example.dat"])
        self.publish(self.submit(first))
        neighbor, neighbor_files = package_body(name="second")
        self.publish(self.submit(neighbor))
        update, updated_files = package_body(version="1.1.0", extra_files={"new/example.dat": b"new\n"},
                                             install_extra=["new/example.dat"])
        self.publish(self.submit(update))
        output = self.output()
        self.assertNotIn("s/old/example.dat", output)
        for path, data in {**updated_files, **neighbor_files}.items():
            self.assertEqual(data, output[f"s/{path}"])
        self.assertEqual("1.1.0", self.current()["version"])
        self.assertEqual(2, len(self.registry.archive_state()["packages"]))

    def test_shared_file_collision_keeps_approval_and_records_failed_delivery(self):
        shared = {"shared.ado": b"program shared\nend\n"}
        body, _ = package_body(extra_files=shared, install_extra=["shared.ado"])
        self.publish(self.submit(body))
        before = self.output()
        other, _ = package_body(name="second", extra_files=shared, install_extra=["shared.ado"])
        candidate = self.submit(other)
        self.approve(candidate)
        failed = self.registry.deliver(candidate["id"])
        self.assertEqual("approved", failed["status"])
        self.assertEqual("failed", failed["delivery"]["status"])
        self.assertIn("collision", failed["delivery"]["error"])
        self.assertEqual(before, self.output())
        self.assertEqual("approved", self.registry.get("submissions", candidate["id"])["status"])
        self.assertEqual(["sample"], [item["name"] for item in self.registry.archive_state()["packages"]])
        retried = self.registry.deliver(candidate["id"])
        self.assertEqual("failed", retried["delivery"]["status"])
        self.assertEqual(2, len(retried["delivery"]["attempts"]))
        self.assertEqual(before, self.output())

    def test_older_or_equal_candidate_cannot_replace_current_files(self):
        older = self.submit(version="1.0.0")
        current = self.submit(version="1.1.0")
        duplicate = self.submit(version="1.1.0")
        self.publish(current)
        before = self.output()
        for candidate in (older, duplicate):
            with self.subTest(version=candidate["version"]):
                with self.assertRaises(APIError) as error:
                    self.approve(candidate)
                self.assertEqual(409, error.exception.status)
                self.assertEqual("passed", self.registry.get("submissions", candidate["id"])["status"])
                self.assertEqual(before, self.output())
                self.assertEqual(current["id"], self.current()["submission_id"])

    def test_failed_and_returned_revisions_cannot_be_published(self):
        body, _ = package_body()
        body["metadata"]["dependencies"] = [{"name": "missing", "version": "1.0.0"}]
        failed = self.submit(body)
        self.assertEqual("failed", failed["status"])
        with self.assertRaises(APIError):
            self.approve(failed)
        candidate = self.submit()
        self.registry.review(candidate["id"], {"decision": "request_changes", "reviewer": "Test reviewer",
                                                "note": "Clarify the example."})
        with self.assertRaises(APIError):
            self.approve(candidate)
        revision_body = {"trusted": True, "metadata": deepcopy(candidate["metadata"]),
                         "source_submission_id": candidate["id"], "parent_id": candidate["id"]}
        revision_body["metadata"]["notes"] = "Clarified the example as requested."
        revision = self.submit(revision_body)
        self.assertEqual("passed", revision["status"])
        self.assertEqual({}, self.output())
        self.publish(revision)
        self.assertEqual(revision["id"], self.current()["submission_id"])

    def test_tampered_source_does_not_change_published_files_or_approval(self):
        self.publish(self.submit())
        candidate = self.submit(version="1.1.0")
        before = self.output()
        source = self.registry.data / "submissions" / candidate["id"] / "source.zip"
        source.write_bytes(b"tampered")
        with self.assertRaisesRegex(APIError, "checksum"):
            self.approve(candidate)
        self.assertEqual(before, self.output())
        self.assertEqual("passed", self.registry.get("submissions", candidate["id"])["status"])
        self.assertEqual("1.0.0", self.current()["version"])

    def test_partial_write_failure_rolls_back_output_and_can_be_retried(self):
        self.publish(self.submit())
        candidate = self.submit(version="1.1.0", extra_files={"new.dat": b"new file"},
                                install_extra=["new.dat"])
        before, state = self.output(), self.registry.archive_state()
        approved = self.approve(candidate)
        replace_file = archive_writer.replace_file
        writes = 0

        def fail_after_writing(path, data):
            nonlocal writes
            replace_file(path, data)
            if self.registry.current_archive in path.parents:
                writes += 1
                if writes == 2:
                    raise OSError("Simulated disk write failure")

        with patch.object(archive_writer, "replace_file", side_effect=fail_after_writing):
            failed = self.registry.deliver(candidate["id"])
        self.assertEqual("approved", failed["status"])
        self.assertEqual("failed", failed["delivery"]["status"])
        self.assertIn("Simulated disk write failure", failed["delivery"]["error"])
        self.assertEqual(before, self.output())
        self.assertEqual(state, self.registry.archive_state())
        self.assertEqual("approved", self.registry.get("submissions", candidate["id"])["status"])
        retried = self.registry.deliver(candidate["id"])
        self.assertEqual("delivered", retried["delivery"]["status"])
        self.assertEqual(2, len(retried["delivery"]["attempts"]))
        self.assertEqual(approved["reviewed_at"], retried["reviewed_at"])
        self.assertEqual("1.1.0", self.current()["version"])
        self.assertEqual(b"new file", self.output()["s/new.dat"])

    def test_database_rejection_restores_replaced_and_removed_files(self):
        self.publish(self.submit(extra_files={"old.dat": b"preserve on rollback"},
                                 install_extra=["old.dat"]))
        candidate = self.submit(version="1.1.0")
        before, state = self.output(), self.registry.archive_state()
        approved = self.approve(candidate)
        with self.registry.connect() as db:
            db.execute("""CREATE TRIGGER reject_fixture_release BEFORE INSERT ON releases
                          WHEN NEW.version = '1.1.0'
                          BEGIN SELECT RAISE(ABORT, 'Simulated database rejection'); END""")
        failed = self.registry.deliver(candidate["id"])
        self.assertEqual("approved", failed["status"])
        self.assertEqual("failed", failed["delivery"]["status"])
        self.assertIn("Simulated database rejection", failed["delivery"]["error"])
        self.assertEqual(before, self.output())
        self.assertEqual(state, self.registry.archive_state())
        self.assertEqual("approved", self.registry.get("submissions", candidate["id"])["status"])
        self.assertEqual(["1.0.0"], [item["version"] for item in self.registry.rows("releases")])
        with self.registry.connect() as db:
            db.execute("DROP TRIGGER reject_fixture_release")
        retried = self.registry.deliver(candidate["id"])
        self.assertEqual("delivered", retried["delivery"]["status"])
        self.assertEqual(2, len(retried["delivery"]["attempts"]))
        self.assertEqual(approved["reviewed_at"], retried["reviewed_at"])
        self.assertNotIn("s/old.dat", self.output())

    def test_current_output_and_revision_evidence_survive_restart(self):
        candidate = self.submit()
        self.publish(candidate)
        before, state = self.output(), self.registry.archive_state()
        self.registry.close()
        self.registry = Registry(self.temp.name, runner=passing_runner)
        self.assertEqual(before, self.output())
        self.assertEqual(state, self.registry.archive_state())
        self.assertEqual("approved", self.registry.get("submissions", candidate["id"])["status"])

    def test_startup_does_not_export_legacy_approvals(self):
        candidate = self.submit()
        candidate["status"] = "approved"
        self.registry.save("submissions", candidate)
        legacy = {key: candidate[key] for key in ("name", "version", "metadata", "sha256", "files", "dependency_releases")}
        legacy.update(id="a" * 32, submission_id=candidate["id"], created_at=candidate["created_at"])
        with self.registry.connect() as db:
            db.execute("INSERT INTO releases(id,name,version,value) VALUES(?,?,?,?)",
                       (legacy["id"], legacy["name"], legacy["version"], json.dumps(legacy)))
        self.registry.close()
        self.registry = Registry(self.temp.name, runner=passing_runner)
        self.assertEqual([], self.registry.archive_state()["packages"])
        self.assertEqual({}, self.output())
        self.assertEqual("approved", self.registry.get("submissions", candidate["id"])["status"])


class CurrentArchiveHTTPTest(ArchiveFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.server = make_server(self.registry, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        super().tearDown()

    def request(self, path):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        connection.request("GET", path)
        response = connection.getresponse()
        status, data = response.status, response.read()
        connection.close()
        return status, data

    def test_current_catalog_download_and_stata_files_match_approved_inventory(self):
        body, expected = package_body(extra_files={"data/example.bin": b"\x00\xff\r\n"},
                                      install_extra=["data/example.bin"])
        self.publish(self.submit(body))
        status, data = self.request("/api/archive")
        self.assertEqual(200, status)
        archive = json.loads(data)
        self.assertEqual("local", archive["mode"])
        item = archive["packages"][0]
        self.assertEqual("sample", item["name"])
        self.assertEqual(f"http://127.0.0.1:{self.server.server_port}/archive/s/", item["install_url"])
        status, data = self.request("/api/state")
        self.assertEqual(200, status)
        self.assertEqual(archive, json.loads(data)["archive"])
        status, data = self.request("/api/archive/sample/download")
        self.assertEqual(200, status)
        with zipfile.ZipFile(io.BytesIO(data)) as download:
            names = set(download.namelist())
            self.assertTrue(set(expected).issubset(names))
            self.assertTrue(names.issubset(set(expected) | {"stata.toc"}))
            for path, content in expected.items():
                self.assertEqual(content, download.read(path))
        for path, content in expected.items():
            status, data = self.request(f"/archive/s/{path}")
            self.assertEqual(200, status)
            self.assertEqual(content, data)
        status, data = self.request("/archive/s/stata.toc")
        self.assertEqual(200, status)
        self.assertIn(b"p sample", data)

    def test_http_returns_current_version_and_removes_superseded_files(self):
        old, _ = package_body(extra_files={"removed.dat": b"old"}, install_extra=["removed.dat"])
        self.publish(self.submit(old))
        update, expected = package_body(version="1.1.0")
        self.publish(self.submit(update))
        status, data = self.request("/archive/s/sample.ado")
        self.assertEqual((200, expected["sample.ado"]), (status, data))
        self.assertEqual(404, self.request("/archive/s/removed.dat")[0])
        status, data = self.request("/api/archive/sample/download")
        self.assertEqual(200, status)
        with zipfile.ZipFile(io.BytesIO(data)) as download:
            self.assertEqual(expected["sample.ado"], download.read("sample.ado"))
            self.assertNotIn("removed.dat", download.namelist())

    def test_current_routes_do_not_expose_private_files_or_traversal(self):
        candidate = self.submit()
        self.publish(candidate)
        for path in (
            "/archive/s/smoke.do", "/archive/s/metadata.json", "/archive/s/private-review.txt",
            "/archive/s/../../registry.sqlite3", "/archive/s/%2e%2e/%2e%2e/registry.sqlite3",
            f"/archive/s/../../submissions/{candidate['id']}/source.zip",
            "/archive/s/%2e%2e%2f%2e%2e%2fregistry.sqlite3",
            "/archive/s/%5c..%5c..%5cregistry.sqlite3", "/api/archive/missing/download",
        ):
            with self.subTest(path=path):
                status, _ = self.request(path)
                self.assertIn(status, (400, 404))


if __name__ == "__main__":
    unittest.main()
