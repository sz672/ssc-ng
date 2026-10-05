"""Approval and a recoverable local handoff to today's SSC-style archive.

The adapter deliberately has no remote credentials or GitHub write operation.
Its reviewed payload/manifest is the integration boundary for the existing
archive process once that process's actual input has been agreed.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import re
import sqlite3
import uuid
import zipfile

from .archive import ArchiveJournal, package_files, preflight_current, write_current


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def conflict(message):
    # Registry imports this mixin, so import its public error only at call time.
    from .service import APIError
    return APIError(message, 409)


class DeliveryMixin:
    @contextmanager
    def _delivery_guard(self):
        # The thread lock protects this Registry; flock also serializes separate
        # service processes pointing at the same local data directory.
        with self.lock:
            with (self.data / "delivery.lock").open("a+b") as stream:
                fcntl.flock(stream, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def _checked_source(self, submission, require_approval=False):
        source = self.zip_for(submission)
        fingerprint = hashlib.sha256((submission["sha256"] + encoded(submission["metadata"])).encode()).hexdigest()
        if fingerprint != submission["fingerprint"]:
            raise conflict("Package metadata changed after checks. Submit and check a new revision.")
        if require_approval and (submission["status"] != "approved" or
                                 submission.get("reviewed_fingerprint") != fingerprint):
            raise conflict("Only the exact approved revision can be delivered.")
        return source

    def _check_base(self, submission, packages):
        current = packages.get(submission["name"])
        current_id = current["submission_id"] if current else None
        if submission.get("base_submission_id") != current_id:
            raise conflict("Today's archive changed since this submission was created. Submit a new revision against the current package and review its changes.")
        if current and all(re.fullmatch(r"\d+(?:\.\d+)*", item["version"]) for item in (submission, current)):
            candidate_version, current_version = [tuple(int(part) for part in item["version"].split("."))
                                                   for item in (submission, current)]
            if candidate_version <= current_version:
                raise conflict("Today's archive already contains this version or a newer version. Submit a newer version.")
        return current

    def approve_candidate(self, submission, note):
        """Record an immutable reviewed candidate without writing archive files."""
        with self._delivery_guard():
            self._checked_source(submission)
            self._check_base(submission, self.setting("current_archive") or {})
            # Validate the prospective inventory before reserving this approval.
            try:
                package_files(self.zip_for(submission), submission["name"])
            except ValueError as error:
                raise conflict(str(error)) from None
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                latest = json.loads(db.execute("SELECT value FROM submissions WHERE id=?", (submission["id"],)).fetchone()[0])
                if latest["status"] != "passed" or latest["fingerprint"] != submission["fingerprint"]:
                    raise conflict("Only the exact revision with passing checks can be approved.")
                for row in db.execute("SELECT value FROM submissions"):
                    other = json.loads(row[0])
                    if (other["id"] != submission["id"] and other["status"] == "approved" and
                            other["name"] == submission["name"] and other["version"] == submission["version"]):
                        raise conflict("This package version already has an approved submission.")
                if db.execute("SELECT 1 FROM releases WHERE name=? AND version=?", (submission["name"], submission["version"])).fetchone():
                    raise conflict("This version is already published.")
                owner_row = db.execute("SELECT value FROM settings WHERE key=?", (f"owner:{submission['name']}",)).fetchone()
                if owner_row:
                    owner = json.loads(owner_row[0])
                    if owner.get("email", "").casefold() != submission["metadata"]["email"].casefold():
                        raise conflict("The package maintainer changed before approval. Resolve ownership and submit a new revision.")
                else:
                    owner = {key: submission["metadata"][key] for key in ("maintainer", "email")}
                    db.execute("INSERT INTO settings(key,value) VALUES(?,?)", (f"owner:{submission['name']}", encoded(owner)))
                submission.update(status="approved", review_note=note, reviewed_at=timestamp(),
                                  reviewed_fingerprint=submission["fingerprint"],
                                  reviewed_dependency_releases=[dict(dep) for dep in submission.get("dependency_releases", [])],
                                  delivery={"status": "pending", "attempts": [], "error": None})
                db.execute("UPDATE submissions SET value=? WHERE id=?", (encoded(submission), submission["id"]))
                self.event("approval", f"Approved {submission['name']}@{submission['version']}; delivery to today's local archive is pending.", db)
            return submission

    def handoff_state(self):
        return {"mode": "local", "root": str(self.current_archive),
                "layout": "<letter>/<package>.pkg + inventory files",
                "production_connected": False,
                "boundary": "Deliver reviewed package files to today's archive. The existing archive process owns historical versioning and mirroring.",
                "integration_status": "Local adapter ready; the production input and its operator still need to be confirmed.",
                "reference_url": "https://github.com/ssc-ng/archive/",
                "recovery_errors": getattr(self, "delivery_recovery_errors", [])}

    def handoff_plan(self, identifier):
        with self.lock:
            submission = self.get("submissions", identifier)
            files = package_files(self._checked_source(submission), submission["name"])
            packages = self.setting("current_archive") or {}
            current = packages.get(submission["name"])
            old_hashes = current["file_hashes"] if current else {}
            hashes = {path: hashlib.sha256(data).hexdigest() for path, data in files.items()}
            old, new = set(old_hashes), set(files)
            blocking = []
            delivered = submission.get("delivery", {}).get("status") == "delivered"
            if submission["status"] != "approved":
                blocking.append("Approve the checked candidate before delivery.")
            if not delivered and submission.get("base_submission_id") != (current["submission_id"] if current else None):
                blocking.append("Today's package changed after submission; create a new revision.")
            if not delivered:
                try:
                    preflight_current(self.current_archive, files, current, packages,
                                      {"name": submission["name"], "metadata": submission["metadata"], "files": sorted(files)})
                except (OSError, ValueError) as error:
                    blocking.append(str(error))
                blocking.extend(getattr(self, "delivery_recovery_errors", []))
            return {"mode": "local", "production_connected": False, "destination": str(self.current_archive),
                    "submission_id": identifier, "fingerprint": submission["fingerprint"],
                    "metadata": submission["metadata"], "sha256": submission["sha256"],
                    "expected_base_submission_id": submission.get("base_submission_id"),
                    "current_base_submission_id": current["submission_id"] if current else None,
                    "added": sorted(new - old), "removed": sorted(old - new),
                    "changed": sorted(path for path in old & new if old_hashes[path] != hashes[path]),
                    "unchanged": sorted(path for path in old & new if old_hashes[path] == hashes[path]),
                    "files": [{"path": path, "sha256": hashes[path], "size": len(files[path])} for path in sorted(files)],
                    "blocking_reasons": blocking, "ready": not blocking and not delivered,
                    "index": f"{submission['name'][0]}/stata.toc"}

    def handoff_bundle(self, identifier):
        with self.lock:
            submission = self.get("submissions", identifier)
            files = package_files(self._checked_source(submission, require_approval=True), submission["name"])
            manifest = self.handoff_plan(identifier)
            manifest.update(format="ssc-ng-reviewed-handoff-v1", reviewed_at=submission["reviewed_at"],
                            reviewed_fingerprint=submission["reviewed_fingerprint"],
                            dependency_releases=submission.get("reviewed_dependency_releases", []),
                            note="Apply inventory files and the listed removals through the agreed archive input. Regenerate the package index there; this bundle does not modify a remote archive.")
            receipt = submission.get("delivery", {}).get("receipt")
            if receipt:
                # A delivered bundle must retain the original change set, even
                # after this package or a neighbour has been updated again.
                manifest.update(receipt=receipt, **receipt.get("changes", {}))
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                for path, data in sorted(files.items()):
                    archive.writestr(path, data)
                archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True, indent=2) + "\n")
            return output.getvalue()

    def deliver(self, identifier, body=None):
        """Deliver or retry once. A completed receipt makes repeated calls inert."""
        with self._delivery_guard():
            self._recover_deliveries_locked()
            submission = self.get("submissions", identifier)
            if submission["status"] != "approved":
                raise conflict("Only an approved submission can be delivered.")
            delivery = submission.get("delivery")
            if delivery and delivery["status"] == "delivered":
                return submission
            if not delivery:
                raise conflict("This older approval has no delivery record. Review a new submission before delivering it.")
            attempt = {"id": uuid.uuid4().hex, "at": timestamp(), "status": "delivering"}
            delivery["attempts"].append(attempt)
            delivery.update(status="delivering", error=None)
            self.save("submissions", submission)
            try:
                if self.delivery_recovery_errors:
                    raise conflict("An interrupted local delivery needs recovery before another write: " + "; ".join(self.delivery_recovery_errors))
                source = self._checked_source(submission, require_approval=True)
                packages = self.setting("current_archive") or {}
                previous = self._check_base(submission, packages)
                if submission.get("reviewed_dependency_releases", []) != submission.get("dependency_releases", []):
                    raise conflict("The checked dependency records changed after approval. Submit and check a new revision.")
                for dep in submission.get("dependency_releases", []):
                    release = self.release(dep["name"], dep["version"])
                    if release["sha256"] != dep["sha256"]:
                        raise conflict("A dependency changed after checking. Submit and check a new revision.")
                    current_dependency = packages.get(dep["name"])
                    if (not current_dependency or current_dependency["version"] != dep["version"] or
                            current_dependency["submission_id"] != release["submission_id"]):
                        raise conflict(f"Dependency {dep['name']} changed in today's archive after checking. Submit and check a new revision against its current version.")
                    self.zip_for(self.get("submissions", release["submission_id"]))
                    for relative in current_dependency["files"]:
                        self.archive_file(relative)
                files = package_files(source, submission["name"])
                hashes = {path: hashlib.sha256(data).hexdigest() for path, data in files.items()}
                delivered_at = timestamp()
                plan = self.handoff_plan(identifier)
                receipt = {"id": attempt["id"], "delivered_at": delivered_at, "mode": "local",
                           "destination": str(self.current_archive), "production_connected": False,
                           "submission_id": identifier, "name": submission["name"], "version": submission["version"],
                           "fingerprint": submission["fingerprint"], "sha256": submission["sha256"],
                           "dependency_releases": submission.get("reviewed_dependency_releases", []),
                           "previous_submission_id": previous["submission_id"] if previous else None,
                           "files": sorted(files), "file_hashes": hashes,
                           "removed_files": plan["removed"],
                           "changes": {key: plan[key] for key in ("added", "changed", "removed", "unchanged")}}
                publication = {"name": submission["name"], "version": submission["version"],
                               "submission_id": identifier, "metadata": submission["metadata"],
                               "published_at": delivered_at, "files": sorted(files), "file_hashes": hashes,
                               "receipt_id": attempt["id"], "fingerprint": submission["fingerprint"]}
                release = {key: submission[key] for key in ("name", "version", "metadata", "sha256", "files", "dependency_releases")}
                release.update(id=uuid.uuid4().hex, submission_id=identifier, created_at=delivered_at)
                journal = ArchiveJournal(self.data / "delivery-journals" / f"{attempt['id']}.json",
                                         {"attempt_id": attempt["id"], "submission_id": identifier})
                with write_current(self.current_archive, files, previous, packages, publication, journal=journal):
                    attempt.update(status="delivered", completed_at=delivered_at)
                    delivery.update(status="delivered", receipt=receipt, error=None)
                    submission.update(archive_files=sorted(files), published_at=delivered_at)
                    with self.connect() as db:
                        db.execute("INSERT INTO releases(id,name,version,value) VALUES(?,?,?,?)",
                                   (release["id"], release["name"], release["version"], encoded(release)))
                        db.execute("UPDATE submissions SET value=? WHERE id=?", (encoded(submission), identifier))
                        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
                                   ("current_archive", encoded({**packages, submission["name"]: publication})))
                        self.event("delivery", f"Delivered {submission['name']}@{submission['version']} to today's local archive (receipt {attempt['id'][:8]}).", db)
                return submission
            except (OSError, ValueError, sqlite3.Error) as error:
                # Reload the durable approval: never retain a receipt assembled
                # in memory for a transaction whose writes were rolled back.
                submission = self.get("submissions", identifier)
                delivery = submission["delivery"]
                if delivery.get("status") == "delivered" and delivery.get("receipt", {}).get("id") == attempt["id"]:
                    return submission
                delivery.update(status="failed", error=str(error))
                delivery["attempts"][-1].update(status="failed", completed_at=timestamp(), error=str(error))
                self.save("submissions", submission)
                self.event("delivery_failed", f"Delivery failed for {submission['name']}@{submission['version']}: {error}")
                return submission

    def recover_deliveries(self):
        with self._delivery_guard():
            return self._recover_deliveries_locked()

    def _recover_deliveries_locked(self):
        errors, pending_ids = [], set()
        for path in sorted((self.data / "delivery-journals").glob("*.json")):
            journal = ArchiveJournal(path)
            submission = None
            try:
                details = journal.load()
                pending_ids.add(details["submission_id"])
                submission = self.get("submissions", details["submission_id"])
                receipt = submission.get("delivery", {}).get("receipt", {})
                committed = (submission.get("delivery", {}).get("status") == "delivered" and
                             receipt.get("id") == details["attempt_id"])
                if not committed:
                    journal.rollback(self.current_archive)
                journal.clear()
                if not committed:
                    self._record_interrupted_delivery(submission)
            except (OSError, ValueError, KeyError, TypeError) as error:
                errors.append(f"{path.name}: {error}")
                if submission and submission.get("delivery", {}).get("status") != "delivered":
                    delivery = submission["delivery"]
                    message = f"Interrupted delivery needs operator recovery: {error}"
                    if delivery.get("error") != message:
                        delivery.update(status="failed", error=message)
                        if delivery.get("attempts"):
                            delivery["attempts"][-1].update(status="failed", completed_at=timestamp(), error=message)
                        self.save("submissions", submission)
                        self.event("delivery_recovery_failed", f"Recovery needs attention for {submission['name']}@{submission['version']}: {error}")
        for submission in self.rows("submissions"):
            if submission.get("delivery", {}).get("status") == "delivering" and submission["id"] not in pending_ids:
                self._record_interrupted_delivery(submission)
        self.delivery_recovery_errors = errors
        return errors

    def _record_interrupted_delivery(self, submission):
        delivery = submission["delivery"]
        message = "The service stopped before delivery committed. Previous archive files were restored; retry delivery."
        delivery.update(status="failed", error=message)
        if delivery.get("attempts"):
            delivery["attempts"][-1].update(status="failed", completed_at=timestamp(), error=message)
        self.save("submissions", submission)
        self.event("delivery_recovered", f"Recovered interrupted delivery of {submission['name']}@{submission['version']}.")
