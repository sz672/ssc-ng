"""Persistent, loopback-only API. Submitted Stata code requires explicit trust."""
from __future__ import annotations

import argparse
import base64
import binascii
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
import threading
from urllib.parse import unquote, urlsplit
import uuid
import zipfile

from .packages import discover_stata, run_stata, validate_bundle, _safe_path
from .archive import safe_destination, toc
from .workflow import WorkflowMixin
from .delivery import DeliveryMixin
from .intake import inspect_upload, check_reproduction

ROOT = Path(__file__).resolve().parent.parent
APP_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
API_REVISION = 1
NAME = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class APIError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def metadata_checked(value):
    if not isinstance(value, dict):
        raise APIError("Package metadata must be an object.")
    result = {}
    for key in ("name", "version", "title", "maintainer", "email", "stata", "license", "notes"):
        item = value.get(key)
        if not isinstance(item, str) or not item.strip() or len(item) > (8000 if key == "notes" else 250):
            raise APIError(f"Provide a valid {key}.")
        result[key] = item.strip()
    if not NAME.fullmatch(result["name"]) or not VERSION.fullmatch(result["version"]):
        raise APIError("Use a lowercase Stata package name and a release version such as 1.0.0.")
    if not re.fullmatch(r"\d{1,2}(?:\.\d)?", result["stata"]):
        raise APIError("Minimum Stata version must be numeric, such as 16.0.")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", result["email"]):
        raise APIError("Provide a valid maintainer email.")
    dependencies = value.get("dependencies", [])
    if not isinstance(dependencies, list) or len(dependencies) > 30:
        raise APIError("Dependencies must be a list of at most 30 exact package versions.")
    result["dependencies"] = []
    seen = set()
    for dep in dependencies:
        if not isinstance(dep, dict) or not NAME.fullmatch(str(dep.get("name", ""))) or not VERSION.fullmatch(str(dep.get("version", ""))):
            raise APIError("Every dependency needs a package name and an exact X.Y.Z version.")
        if dep["name"] in seen or dep["name"] == result["name"]:
            raise APIError("Duplicate dependencies and self-dependencies are not supported.")
        seen.add(dep["name"])
        result["dependencies"].append({"name": dep["name"], "version": dep["version"]})
    result["dependencies"].sort(key=lambda dep: dep["name"])
    if value.get("test_file"):
        if not isinstance(value["test_file"], str):
            raise APIError("Test file must be a safe path inside the ZIP.")
        try:
            test_file = _safe_path(value["test_file"])
        except (ValueError, TypeError):
            raise APIError("Test file must be a safe path inside the ZIP.") from None
        if not test_file.endswith(".do"):
            raise APIError("Choose a .do file for the executable example.")
        result["test_file"] = test_file
    if value.get("source_url"):
        source_url = value["source_url"]
        if not isinstance(source_url, str) or len(source_url) > 2000:
            raise APIError("Provide a valid public project URL.")
        url = urlsplit(source_url)
        if url.scheme not in ("https", "http") or not url.hostname or url.username or url.password:
            raise APIError("Project URL must use HTTP(S) without embedded credentials.")
        result["source_url"] = source_url
    return result


class Registry(WorkflowMixin, DeliveryMixin):
    def __init__(self, data_dir, stata_path=None, runner=run_stata):
        self.data = Path(data_dir).resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data / "registry.sqlite3"
        self.current_archive = self.data / "current-archive"
        self.lock = threading.RLock()
        self.stata_lock = threading.Lock()
        self.runner = runner
        self.stata_path = stata_path or discover_stata()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stata-check")
        self.futures = []
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS submissions(id TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS releases(id TEXT PRIMARY KEY, name TEXT NOT NULL, version TEXT NOT NULL, value TEXT NOT NULL, UNIQUE(name,version));
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, kind TEXT, message TEXT);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS mailbox(id TEXT PRIMARY KEY, submission_id TEXT NOT NULL, value TEXT NOT NULL);
            """)
        # An interrupted process never leaves a candidate looking checked.
        for submission in self.rows("submissions"):
            if submission["status"] in ("queued", "running"):
                submission.update(status="unavailable", log="The service stopped before checks completed. Submit a new revision to retry.")
                self.save("submissions", submission)
        self.recover_deliveries()
        from examples.build_examples import build_all
        self.examples = build_all(self.data / "examples")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            with db:
                yield db
        finally:
            db.close()

    def rows(self, table):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(f"SELECT value FROM {table} ORDER BY rowid DESC")]

    def save(self, table, item):
        with self.connect() as db:
            db.execute(f"INSERT OR REPLACE INTO {table}(id,value) VALUES(?,?)", (item["id"], encoded(item)))

    def get(self, table, identifier):
        with self.connect() as db:
            row = db.execute(f"SELECT value FROM {table} WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise APIError("Record not found.", 404)
        return json.loads(row[0])

    def release(self, name, version):
        with self.connect() as db:
            row = db.execute("SELECT value FROM releases WHERE name=? AND version=?", (name, version)).fetchone()
        if not row:
            raise APIError(f"Approved release {name}@{version} was not found.", 404)
        return json.loads(row[0])

    def event(self, kind, message, db=None):
        if db is not None:
            db.execute("INSERT INTO events(created_at,kind,message) VALUES(?,?,?)", (now(), kind, message))
        else:
            with self.connect() as connection:
                self.event(kind, message, connection)

    def setting(self, key):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_setting(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, encoded(value)))

    def state(self, base_url):
        with self.lock:
            return {"version": APP_VERSION, "api_revision": API_REVISION,
                    "stata": {"available": bool(self.stata_path), "path": self.stata_path},
                    "submissions": [self.public_submission(item) for item in self.rows("submissions")],
                    "archive": self.archive_state(base_url), "handoff": self.handoff_state(), "owners": self.owners(),
                    "examples": [{k: e[k] for k in ("id", "label", "metadata")} for e in self.examples]}

    def archive_state(self, base_url=""):
        with self.lock:
            packages = self.setting("current_archive") or {}
            items = []
            for name, item in sorted(packages.items()):
                items.append({**item, "install_url": f"{base_url}/archive/{name[0]}/",
                              "download_url": f"/api/archive/{name}/download"})
            return {"root": str(self.current_archive), "mode": "local", "packages": items}

    def archive_file(self, relative):
        with self.lock:
            packages = self.setting("current_archive") or {}
            expected = None
            if re.fullmatch(r"[a-z]/stata\.toc", relative) and any(name[0] == relative[0] for name in packages):
                expected = hashlib.sha256(toc(packages, relative[0])).hexdigest()
            else:
                for item in packages.values():
                    if relative in item["file_hashes"]:
                        expected = item["file_hashes"][relative]
                        break
            if expected is None:
                raise APIError("Current archive file not found.", 404)
            try:
                data = safe_destination(self.current_archive, relative).read_bytes()
            except (ValueError, OSError) as error:
                raise APIError(f"Current archive file is unavailable: {error}", 409) from None
            if hashlib.sha256(data).hexdigest() != expected:
                raise APIError("Current archive checksum does not match the approved file.", 409)
            return data

    def archive_download(self, name):
        with self.lock:
            item = (self.setting("current_archive") or {}).get(name)
            if not item:
                raise APIError("Current package not found.", 404)
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                for relative in item["files"]:
                    archive.writestr(relative.split("/", 1)[1], self.archive_file(relative))
            return output.getvalue()

    def zip_for(self, submission):
        path = self.data / "submissions" / submission["id"] / "source.zip"
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != submission["sha256"]:
            raise APIError("Stored package checksum does not match; this archive cannot be used.", 409)
        return data

    def submit(self, body):
        if body.get("trusted") is not True:
            raise APIError("Confirm that you trust the package before running its Stata code on this Mac.")
        metadata = metadata_checked(body.get("metadata"))
        for key in ("source_submission_id", "parent_id"):
            if body.get(key) is not None and (not isinstance(body[key], str) or not re.fullmatch(r"[a-f0-9]{32}", body[key])):
                raise APIError(f"Provide a valid {key}.")
        if body.get("source_submission_id"):
            source = self.get("submissions", body["source_submission_id"])
            if body.get("parent_id") != source["id"]:
                raise APIError("A reused bundle must reference its parent submission.")
            zip_bytes = self.zip_for(source)
        else:
            try:
                zip_bytes = base64.b64decode(body.get("zip_base64", ""), validate=True)
            except (ValueError, TypeError, binascii.Error):
                raise APIError("Upload a valid ZIP file.") from None
        if len(zip_bytes) > 10 * 1024 * 1024:
            raise APIError("Package ZIP exceeds the 10 MB limit.", 413)
        try:
            validation = validate_bundle(zip_bytes, metadata)
        except ValueError as error:
            raise APIError(str(error)) from None
        sha256 = hashlib.sha256(zip_bytes).hexdigest()
        fingerprint = hashlib.sha256((sha256 + encoded(metadata)).encode()).hexdigest()
        with self.lock:
            owner = self.owner_for(metadata["name"])
            if owner and owner["email"].casefold() != metadata["email"].casefold():
                raise APIError("This package already has a recorded maintainer. Use that maintainer's email; ownership changes need operator review.", 409)
            if any(r["name"] == metadata["name"] and r["version"] == metadata["version"] for r in self.rows("releases")):
                raise APIError("That package version is already published. Choose a new version.", 409)
            parent_id = body.get("parent_id")
            current = (self.setting("current_archive") or {}).get(metadata["name"])
            base_id = current["submission_id"] if current else None
            if any(s["name"] == metadata["name"] and s["version"] == metadata["version"] and s["status"] == "approved" and s["id"] != parent_id for s in self.rows("submissions")):
                raise APIError("That package version already has an approved submission. Deliver it or revise it before submitting again.", 409)
            if parent_id:
                parent = self.get("submissions", parent_id)
                if parent["name"] != metadata["name"]:
                    raise APIError("A revision must keep the same package name.")
                revisable_approval = parent["status"] == "approved" and parent.get("delivery", {}).get("status") in ("pending", "failed")
                if parent["status"] in ("queued", "running", "superseded") or (parent["status"] == "approved" and not revisable_approval):
                    raise APIError("Only a completed, unpublished submission can be revised.", 409)
                if parent["fingerprint"] == fingerprint and parent["status"] not in ("failed", "unavailable") and parent.get("base_submission_id") == base_id:
                    raise APIError("Change the package files or metadata before submitting a revision.", 409)
            identifier = uuid.uuid4().hex
            folder = self.data / "submissions" / identifier
            folder.mkdir(parents=True)
            (folder / "source.zip").write_bytes(zip_bytes)
            submission = {"id": identifier, "parent_id": parent_id, "name": metadata["name"], "version": metadata["version"],
                          "base_submission_id": base_id, "kind": "update" if owner or current else "new",
                          "metadata": metadata, "sha256": sha256, "fingerprint": fingerprint, "created_at": now(),
                          "status": "queued" if validation["passed"] else "failed", "checks": validation["checks"],
                          "files": validation["files"], "log": "", "review_note": "", "dependency_releases": []}
            self.save("submissions", submission)
            if parent_id:
                parent["superseded_by"] = identifier
                if parent["status"] == "approved":
                    parent["status"] = "superseded"
                    parent["delivery"]["status"] = "cancelled"
                self.save("submissions", parent)
            submission = self.request_confirmation(identifier)
            self.event("submission", f"Submitted {submission['name']}@{submission['version']} ({identifier[:8]}).")
            if validation["passed"]:
                self.futures.append(self.executor.submit(self.check, identifier))
        return submission

    def dependencies(self, metadata):
        ordered, visited, visiting, versions = [], set(), set(), {}

        def visit(dep):
            name, version = dep["name"], dep["version"]
            key = (name, version)
            if name == metadata["name"] or key in visiting:
                raise APIError("Dependency cycle detected.")
            if name in versions and versions[name] != version:
                raise APIError(f"Conflicting pinned versions for dependency {name}.")
            versions[name] = version
            if key in visited:
                return
            if len(visited) + len(visiting) >= 100:
                raise APIError("Dependency graph exceeds the pilot's 100-package limit.")
            release = self.release(name, version)
            visiting.add(key)
            for child in release["metadata"]["dependencies"]:
                visit(child)
            visiting.remove(key)
            visited.add(key)
            ordered.append(release)

        for dep in metadata["dependencies"]:
            visit(dep)
        return ordered

    def check(self, identifier):
        with self.lock:
            submission = self.get("submissions", identifier)
            submission["status"] = "running"
            self.save("submissions", submission)
        try:
            try:
                deps = self.dependencies(submission["metadata"])
                published = self.setting("current_archive") or {}
                for dep in deps:
                    current = published.get(dep["name"])
                    if not current or current["submission_id"] != dep["submission_id"]:
                        raise APIError(f"Dependency {dep['name']}@{dep['version']} is not the version delivered to today's archive.")
                    for relative in current["files"]:
                        self.archive_file(relative)
            except APIError as error:
                submission["checks"].append({"name": "Dependencies", "status": "failed", "severity": "error", "code": "dependency-resolution", "detail": str(error), "remedy": "Deliver each declared dependency first, or correct its exact package name and version, then submit a revision."})
                submission.update(status="failed", log="Stata was not run because dependency resolution failed.")
                return
            submission["dependency_releases"] = [{"name": dep["name"], "version": dep["version"], "sha256": dep["sha256"]} for dep in deps]
            submission["checks"].append({"name": "Dependencies", "status": "passed", "detail": f"Resolved {len(deps)} approved dependencies for checks."})
            dependency_bundles = [(dep["metadata"], self.zip_for(self.get("submissions", dep["submission_id"]))) for dep in deps]
            with self.stata_lock:
                result = self.runner(self.zip_for(submission), submission["metadata"], dependency_bundles,
                                     self.data / "jobs" / identifier, self.stata_path)
            submission.update(status=result["status"], log=result["log"], stata_version=result.get("stata_version"))
            submission["checks"].extend(result["checks"])
        except Exception as error:
            submission.update(status="unavailable", log=f"Checks could not complete: {type(error).__name__}: {error}")
        finally:
            with self.lock:
                latest = self.get("submissions", identifier)
                for key in ("status", "checks", "log", "stata_version", "dependency_releases"):
                    if key in submission:
                        latest[key] = submission[key]
                latest["checked_fingerprint"] = submission["fingerprint"]
                latest["checked_at"] = now()
                self.save("submissions", latest)
            self.event("checks", f"Checks {submission['status']}: {submission['name']}@{submission['version']}.")

    def review(self, identifier, body):
        with self.lock:
            submission = self.get("submissions", identifier)
            decision, note = body.get("decision"), str(body.get("note", "")).strip()
            if len(note) > 8000:
                raise APIError("Review note is too long.")
            reviewer = body.get("reviewer", "")
            if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 100:
                raise APIError("Provide the reviewer's name for the decision record.")
            reviewer = reviewer.strip()
            if decision == "request_changes":
                if not note:
                    raise APIError("Explain the changes needed.")
                if submission["status"] not in ("passed", "failed", "unavailable"):
                    raise APIError("This submission cannot be returned for changes.", 409)
                submission.update(status="changes_requested", review_note=note, reviewer=reviewer, reviewed_at=now())
                self.save("submissions", submission)
                self.event("review", f"Changes requested for {submission['name']}@{submission['version']}: {note}")
                return submission
            if decision != "approve":
                raise APIError("Choose approve or request_changes.")
            if submission["status"] != "passed":
                raise APIError("Only the exact revision with passing checks can be approved.", 409)
            if submission.get("superseded_by"):
                raise APIError("A newer revision exists. Review that revision instead.", 409)
            confirmation = submission.get("confirmation", {})
            owner = self.owner_for(submission["name"])
            if confirmation.get("status") != "verified" or confirmation.get("fingerprint") != submission["fingerprint"]:
                raise APIError("Confirm the maintainer for this exact submission before approval.", 409)
            if owner and owner["email"].casefold() != confirmation.get("email", "").casefold():
                raise APIError("The recorded maintainer changed. Submit a new revision for confirmation.", 409)
            expected = hashlib.sha256((submission["sha256"] + encoded(submission["metadata"])).encode()).hexdigest()
            if expected != submission["fingerprint"] or submission.get("checked_fingerprint") != expected:
                raise APIError("The candidate differs from its checked revision. Submit and check a new revision.", 409)
            self.zip_for(submission)
            for dep in submission["dependency_releases"]:
                current = self.release(dep["name"], dep["version"])
                if current["sha256"] != dep["sha256"]:
                    raise APIError("A checked dependency changed. Submit a new revision.", 409)
                self.zip_for(self.get("submissions", current["submission_id"]))
            submission["reviewer"] = reviewer
            result = self.approve_candidate(submission, note)
            return result

    def close(self):
        self.executor.shutdown(wait=True)


class Handler(BaseHTTPRequestHandler):
    server_version = f"SSCNG/{APP_VERSION}"

    @property
    def registry(self):
        return self.server.registry

    def log_message(self, format, *args):
        # Avoid logging submitted contact details, source code, and query strings.
        pass

    def send(self, data, status=200, content_type="application/json; charset=utf-8", download=None):
        if not isinstance(data, bytes):
            data = encoded(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        self.wfile.write(data)

    def check_host(self):
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if host not in allowed:
            raise APIError("Use this service through localhost or 127.0.0.1.", 403)
        return "http://" + host

    def do_GET(self):
        try:
            base = self.check_host()
            path = unquote(urlsplit(self.path).path)
            if path == "/api/state":
                return self.send(self.registry.state(base))
            if path == "/api/archive":
                return self.send(self.registry.archive_state(base))
            if path == "/api/handoff":
                return self.send(self.registry.handoff_state())
            if path == "/api/demo-mailbox":
                return self.send(self.registry.demo_mailbox())
            match = re.fullmatch(r"/api/archive/([a-z][a-z0-9_]{0,31})/download", path)
            if match:
                return self.send(self.registry.archive_download(match[1]), content_type="application/zip", download=f"{match[1]}-current.zip")
            if path.startswith("/archive/"):
                relative = path[len("/archive/"):]
                if re.fullmatch(r"[a-z]/", relative):
                    relative += "stata.toc"
                return self.send(self.registry.archive_file(relative), content_type="application/octet-stream")
            if path == "/api/examples":
                return self.send({"examples": self.registry.state(base)["examples"]})
            match = re.fullmatch(r"/api/submissions/([a-f0-9]{32})/download", path)
            if match:
                submission = self.registry.get("submissions", match[1])
                return self.send(self.registry.zip_for(submission), content_type="application/zip", download=f"{submission['name']}-{submission['version']}.zip")
            match = re.fullmatch(r"/api/submissions/([a-f0-9]{32})/(comparison|reproduce|handoff|receipt|plan)", path)
            if match:
                identifier, action = match[1], match[2]
                submission = self.registry.get("submissions", identifier)
                if action == "comparison":
                    return self.send(self.registry.comparison(identifier))
                if action == "plan":
                    return self.send(self.registry.handoff_plan(identifier))
                if action == "receipt":
                    receipt = submission.get("delivery", {}).get("receipt")
                    if not receipt:
                        raise APIError("A delivery receipt is available after successful delivery.", 409)
                    return self.send(receipt, download=f"{submission['name']}-delivery-receipt.json")
                content = self.registry.handoff_bundle(identifier) if action == "handoff" else check_reproduction(self.registry.zip_for(submission), submission["metadata"])
                return self.send(content, content_type="application/zip", download=f"{submission['name']}-{action}.zip")
            static = {"/": ("index.html", "text/html"), "/index.html": ("index.html", "text/html"), "/assets/app.js": ("assets/app.js", "text/javascript"), "/assets/styles.css": ("assets/styles.css", "text/css")}
            if path in static:
                filename, mime = static[path]
                return self.send((ROOT / filename).read_bytes(), content_type=mime + "; charset=utf-8")
            raise APIError("Not found.", 404)
        except APIError as error:
            self.send({"error": str(error)}, error.status)
        except (ValueError, OSError) as error:
            self.send({"error": str(error)}, 500)

    def do_POST(self):
        try:
            base = self.check_host()
            origin = self.headers.get("Origin")
            if origin and origin != base:
                raise APIError("Cross-origin writes are not allowed.", 403)
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                raise APIError("Cross-site writes are not allowed.", 403)
            if self.headers.get_content_type() != "application/json":
                raise APIError("Use application/json for API requests.", 415)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise APIError("Invalid request length.") from None
            if not 0 < length <= 15 * 1024 * 1024:
                raise APIError("Request is empty or exceeds the 15 MB limit.", 413)
            try:
                body = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeDecodeError):
                raise APIError("Request must contain valid JSON.") from None
            if not isinstance(body, dict):
                raise APIError("Request must be an object.")
            path = unquote(urlsplit(self.path).path)
            if path == "/api/intake/inspect":
                try:
                    source = base64.b64decode(body.get("zip_base64", ""), validate=True)
                except (ValueError, TypeError, binascii.Error):
                    raise APIError("Select a valid package ZIP to read its metadata.") from None
                if len(source) > 10 * 1024 * 1024:
                    raise APIError("Package ZIP exceeds the 10 MB limit.", 413)
                return self.send(inspect_upload(source))
            if path == "/api/submissions":
                return self.send({"submission": self.registry.submit(body)}, 201)
            match = re.fullmatch(r"/api/examples/([a-z0-9.-]+)/submit", path)
            if match:
                example = next((e for e in self.registry.examples if e["id"] == match[1]), None)
                if not example:
                    raise APIError("Example not found.", 404)
                return self.send({"submission": self.registry.submit({"trusted": body.get("trusted"), "metadata": example["metadata"], "zip_base64": base64.b64encode(Path(example["path"]).read_bytes()).decode()})}, 201)
            match = re.fullmatch(r"/api/submissions/([a-f0-9]{32})/review", path)
            if match:
                return self.send({"submission": self.registry.review(match[1], body)})
            match = re.fullmatch(r"/api/submissions/([a-f0-9]{32})/(confirm|confirmation|deliver)", path)
            if match:
                if match[2] == "confirm":
                    submission = self.registry.confirm(match[1], body)
                elif match[2] == "confirmation":
                    submission = self.registry.request_confirmation(match[1])
                else:
                    submission = self.registry.deliver(match[1], body)
                return self.send({"submission": submission})
            raise APIError("Not found.", 404)
        except APIError as error:
            self.send({"error": str(error)}, error.status)
        except (ValueError, OSError) as error:
            self.send({"error": str(error)}, 500)


def make_server(registry, port=8765):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.registry = registry
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".sscng")
    parser.add_argument("--stata", help="Path to the Stata executable")
    args = parser.parse_args()
    registry = Registry(args.data_dir, args.stata)
    server = make_server(registry, args.port)
    print(f"SSC-NG submission demo: http://127.0.0.1:{server.server_port}", flush=True)
    print(f"Data: {registry.data}\nToday's local archive: {registry.current_archive}\nStata: {registry.stata_path or 'unavailable'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        registry.close()


if __name__ == "__main__":
    main()
