"""Persistent, loopback-only API. Submitted Stata code requires explicit trust."""
from __future__ import annotations

import argparse
import base64
import binascii
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import difflib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
import threading
from urllib.parse import unquote, urlsplit
import uuid

from .packages import bundle_files, discover_stata, run_stata, validate_bundle

ROOT = Path(__file__).resolve().parent.parent
APP_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
NAME = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def version_key(release):
    return tuple(int(part) for part in release["version"].split("."))


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
    return result


class Registry:
    def __init__(self, data_dir, stata_path=None, runner=run_stata):
        self.data = Path(data_dir).resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data / "registry.sqlite3"
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
                CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS transfers(id TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
        # An interrupted process never leaves a candidate looking checked.
        for submission in self.rows("submissions"):
            if submission["status"] in ("queued", "running"):
                submission.update(status="unavailable", log="The service stopped before checks completed. Submit a new revision to retry.")
                self.save("submissions", submission)
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
        releases = self.rows("releases")
        for release in releases:
            release["install_url"] = f"{base_url}/packages/{release['name']}/{release['version']}/"
            release["download_url"] = f"/api/releases/{release['name']}/{release['version']}/download"
        with self.connect() as db:
            events = [dict(zip(("id", "created_at", "kind", "message"), row)) for row in db.execute("SELECT id,created_at,kind,message FROM events ORDER BY id DESC LIMIT 200")]
        return {"version": APP_VERSION, "stata": {"available": bool(self.stata_path), "path": self.stata_path},
                "submissions": self.rows("submissions"), "releases": releases, "events": events,
                "snapshots": self.rows("snapshots"), "transfers": self.rows("transfers"),
                "environment": self.setting("environment"),
                "examples": [{k: e[k] for k in ("id", "label", "metadata")} for e in self.examples]}

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
            if any(r["name"] == metadata["name"] and r["version"] == metadata["version"] for r in self.rows("releases")):
                raise APIError("That package version is already published. Choose a new version.", 409)
            parent_id = body.get("parent_id")
            if parent_id:
                parent = self.get("submissions", parent_id)
                if parent["name"] != metadata["name"]:
                    raise APIError("A revision must keep the same package name.")
                if parent["status"] in ("queued", "running", "approved"):
                    raise APIError("Only a completed, unpublished submission can be revised.", 409)
                if parent["fingerprint"] == fingerprint and parent["status"] not in ("failed", "unavailable"):
                    raise APIError("Change the package files or metadata before submitting a revision.", 409)
            identifier = uuid.uuid4().hex
            folder = self.data / "submissions" / identifier
            folder.mkdir(parents=True)
            (folder / "source.zip").write_bytes(zip_bytes)
            submission = {"id": identifier, "parent_id": parent_id, "name": metadata["name"], "version": metadata["version"],
                          "metadata": metadata, "sha256": sha256, "fingerprint": fingerprint, "created_at": now(),
                          "status": "queued" if validation["passed"] else "failed", "checks": validation["checks"],
                          "files": validation["files"], "log": "", "review_note": "", "dependency_releases": []}
            self.save("submissions", submission)
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
        submission = self.get("submissions", identifier)
        submission["status"] = "running"
        self.save("submissions", submission)
        try:
            try:
                deps = self.dependencies(submission["metadata"])
            except APIError as error:
                submission["checks"].append({"name": "Dependencies", "status": "failed", "detail": str(error)})
                submission.update(status="failed", log="Stata was not run because dependency resolution failed.")
                return
            submission["dependency_releases"] = [{"name": dep["name"], "version": dep["version"], "sha256": dep["sha256"]} for dep in deps]
            submission["checks"].append({"name": "Dependencies", "status": "passed", "detail": f"Resolved {len(deps)} archived dependencies."})
            dependency_bundles = [(dep["metadata"], self.zip_for(self.get("submissions", dep["submission_id"]))) for dep in deps]
            with self.stata_lock:
                result = self.runner(self.zip_for(submission), submission["metadata"], dependency_bundles,
                                     self.data / "jobs" / identifier, self.stata_path)
            submission.update(status=result["status"], log=result["log"], stata_version=result.get("stata_version"))
            submission["checks"].extend(result["checks"])
        except Exception as error:
            submission.update(status="unavailable", log=f"Checks could not complete: {type(error).__name__}: {error}")
        finally:
            self.save("submissions", submission)
            self.event("checks", f"Checks {submission['status']}: {submission['name']}@{submission['version']}.")

    def review(self, identifier, body):
        with self.lock:
            submission = self.get("submissions", identifier)
            decision, note = body.get("decision"), str(body.get("note", "")).strip()
            if len(note) > 8000:
                raise APIError("Review note is too long.")
            if decision == "request_changes":
                if not note:
                    raise APIError("Explain the changes needed.")
                if submission["status"] not in ("passed", "failed", "unavailable"):
                    raise APIError("This submission cannot be returned for changes.", 409)
                submission.update(status="changes_requested", review_note=note)
                self.save("submissions", submission)
                self.event("review", f"Changes requested for {submission['name']}@{submission['version']}: {note}")
                return submission
            if decision != "approve":
                raise APIError("Choose approve or request_changes.")
            if submission["status"] != "passed":
                raise APIError("Only the exact revision with passing checks can be approved.", 409)
            self.zip_for(submission)
            for dep in submission["dependency_releases"]:
                current = self.release(dep["name"], dep["version"])
                if current["sha256"] != dep["sha256"]:
                    raise APIError("An archived dependency changed after checking.", 409)
                self.zip_for(self.get("submissions", current["submission_id"]))
            release = {key: submission[key] for key in ("name", "version", "metadata", "sha256", "files", "dependency_releases")}
            release.update(id=uuid.uuid4().hex, submission_id=identifier, created_at=now())
            submission.update(status="approved", review_note=note)
            try:
                with self.connect() as db:
                    db.execute("INSERT INTO releases(id,name,version,value) VALUES(?,?,?,?)", (release["id"], release["name"], release["version"], encoded(release)))
                    db.execute("UPDATE submissions SET value=? WHERE id=?", (encoded(submission), identifier))
                    self.event("release", f"Approved and archived {release['name']}@{release['version']}.", db)
            except sqlite3.IntegrityError:
                raise APIError("This version is already published.", 409) from None
            return submission

    def snapshot(self):
        with self.lock:
            latest = {}
            for release in sorted(self.rows("releases"), key=version_key):
                latest[release["name"]] = {k: release[k] for k in ("name", "version", "sha256")}
            snapshot = {"id": uuid.uuid4().hex, "created_at": now(), "packages": sorted(latest.values(), key=lambda p: p["name"])}
            self.save("snapshots", snapshot)
            self.event("snapshot", f"Captured the local catalog ({len(latest)} packages).")
            return snapshot

    def daily_snapshot(self):
        # Capture once per UTC day while the local service is running; never invent past captures.
        snapshots = self.rows("snapshots")
        if not snapshots or snapshots[0]["created_at"][:10] != now()[:10]:
            self.snapshot()

    def restore(self, name, version, body):
        if body.get("trusted") is not True:
            raise APIError("Confirm that you trust this package before installing and running it.")
        release = self.release(name, version)
        submission = self.get("submissions", release["submission_id"])
        deps = self.dependencies(release["metadata"])
        dependency_bundles = [(dep["metadata"], self.zip_for(self.get("submissions", dep["submission_id"]))) for dep in deps]
        directory = self.data / "environment" / "jobs" / uuid.uuid4().hex
        with self.stata_lock:
            result = self.runner(self.zip_for(submission), release["metadata"], dependency_bundles, directory, self.stata_path)
        if result["status"] == "passed":
            environment = {"name": name, "version": version, "sha256": release["sha256"], "installed_at": now(), "library_path": result.get("library_path", str(directory / "plus"))}
            self.set_setting("environment", environment)
            self.event("restore", f"Installed and smoke-tested {name}@{version} in the managed local library.")
        else:
            environment = self.setting("environment")
        return {**result, "environment": environment}

    def diff(self, name, version):
        release = self.release(name, version)
        history = sorted((r for r in self.rows("releases") if r["name"] == name), key=version_key)
        position = next(i for i, r in enumerate(history) if r["id"] == release["id"])
        previous = history[position - 1] if position else None
        current_files = bundle_files(self.zip_for(self.get("submissions", release["submission_id"])))
        previous_files = bundle_files(self.zip_for(self.get("submissions", previous["submission_id"]))) if previous else {}
        changed = []
        for path in sorted(current_files.keys() & previous_files.keys()):
            if current_files[path] != previous_files[path]:
                before, after = previous_files[path], current_files[path]
                if len(before) + len(after) > 500_000 or b"\0" in before + after:
                    diff = "Binary or large file changed; download both releases to inspect."
                else:
                    diff = "".join(difflib.unified_diff(before.decode("utf-8", "replace").splitlines(True), after.decode("utf-8", "replace").splitlines(True), fromfile=f"{previous['version']}/{path}", tofile=f"{version}/{path}"))[:100_000]
                changed.append({"path": path, "diff": diff})
        return {"previous_version": previous["version"] if previous else None, "added": sorted(current_files.keys() - previous_files.keys()), "removed": sorted(previous_files.keys() - current_files.keys()), "changed": changed}

    def transfer(self, body):
        name = body.get("name")
        releases = [r for r in self.rows("releases") if r["name"] == name]
        if not releases:
            raise APIError("Publish a package before recording a maintainer transfer.")
        for key in ("to_maintainer", "to_email", "evidence"):
            if not isinstance(body.get(key), str) or not body[key].strip() or len(body[key]) > 8000:
                raise APIError(f"Provide {key}.")
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", body["to_email"]):
            raise APIError("Provide a valid contact email.")
        owner = self.setting(f"owner:{name}") or {"maintainer": releases[0]["metadata"]["maintainer"]}
        transfer = {"id": uuid.uuid4().hex, "name": name, "from_maintainer": owner["maintainer"], "to_maintainer": body["to_maintainer"].strip(), "to_email": body["to_email"].strip(), "evidence": body["evidence"].strip(), "status": "pending", "created_at": now()}
        self.save("transfers", transfer)
        self.event("transfer", f"Recorded a transfer request for {name}; operator review required.")
        return transfer

    def approve_transfer(self, identifier):
        with self.lock:
            transfer = self.get("transfers", identifier)
            if transfer["status"] != "pending":
                raise APIError("This transfer has already been reviewed.", 409)
            releases = [r for r in self.rows("releases") if r["name"] == transfer["name"]]
            owner = self.setting(f"owner:{transfer['name']}") or {"maintainer": releases[0]["metadata"]["maintainer"]}
            if owner["maintainer"] != transfer["from_maintainer"]:
                raise APIError("The maintainer changed since this request was recorded. Record a new request.", 409)
            transfer.update(status="approved", approved_at=now())
            with self.connect() as db:
                db.execute("UPDATE transfers SET value=? WHERE id=?", (encoded(transfer), identifier))
                db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (f"owner:{transfer['name']}", encoded({"maintainer": transfer["to_maintainer"], "email": transfer["to_email"]})))
                self.event("transfer", f"Operator approved {transfer['name']} maintainer transfer to {transfer['to_maintainer']}.", db)
            return transfer

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
            if path == "/api/examples":
                return self.send({"examples": self.registry.state(base)["examples"]})
            match = re.fullmatch(r"/api/submissions/([a-f0-9]{32})/download", path)
            if match:
                submission = self.registry.get("submissions", match[1])
                return self.send(self.registry.zip_for(submission), content_type="application/zip", download=f"{submission['name']}-{submission['version']}.zip")
            match = re.fullmatch(r"/api/releases/([^/]+)/([^/]+)/(download|diff)", path)
            if match:
                release = self.registry.release(match[1], match[2])
                if match[3] == "diff":
                    return self.send(self.registry.diff(match[1], match[2]))
                return self.send(self.registry.zip_for(self.registry.get("submissions", release["submission_id"])), content_type="application/zip", download=f"{release['name']}-{release['version']}.zip")
            match = re.fullmatch(r"/packages/([^/]+)/([^/]+)/(.*)", path)
            if match:
                release = self.registry.release(match[1], match[2])
                filename = match[3] or "stata.toc"
                if filename == "stata.toc":
                    return self.send(f"v 3\nd SSC-NG local approved archive\np {release['name']} {release['name']} {release['version']}\n".encode(), content_type="text/plain; charset=utf-8")
                files = bundle_files(self.registry.zip_for(self.registry.get("submissions", release["submission_id"])))
                if filename not in files:
                    raise APIError("Package file not found.", 404)
                return self.send(files[filename], content_type="text/plain; charset=utf-8")
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
            match = re.fullmatch(r"/api/releases/([^/]+)/([^/]+)/restore", path)
            if match:
                return self.send(self.registry.restore(match[1], match[2], body))
            if path == "/api/snapshots":
                return self.send({"snapshot": self.registry.snapshot()}, 201)
            if path == "/api/transfers":
                return self.send({"transfer": self.registry.transfer(body)}, 201)
            match = re.fullmatch(r"/api/transfers/([a-f0-9]{32})/approve", path)
            if match:
                return self.send({"transfer": self.registry.approve_transfer(match[1])})
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
    stop = threading.Event()

    def captures():
        while not stop.is_set():
            registry.daily_snapshot()
            stop.wait(60)

    threading.Thread(target=captures, daemon=True).start()
    print(f"SSC-NG local pilot: http://127.0.0.1:{server.server_port}", flush=True)
    print(f"Data: {registry.data}\nStata: {registry.stata_path or 'unavailable'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        registry.close()


if __name__ == "__main__":
    main()
