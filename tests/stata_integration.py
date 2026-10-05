#!/usr/bin/env python3
"""Opt-in integration check: executes only this repository's trusted fixtures.

Requires licensed local Stata. Uses a temporary submission registry, today's
archive output, and an isolated library; never the normal Stata PLUS directory.
Run from repository root: python3 tests/stata_integration.py
"""
import base64
import io
import os
from pathlib import Path
import pty
import re
import signal
import subprocess
import sys
import tempfile
import threading
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pilot.intake import check_reproduction
from pilot.packages import bundle_files, run_stata, unpack_bundle
from pilot.service import APIError, Registry, make_server


def main():
    with tempfile.TemporaryDirectory(prefix="sscng-integration-") as directory:
        registry = Registry(directory)
        server = make_server(registry, 0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            def wait(candidate):
                registry.executor.submit(lambda: None).result(timeout=90)
                return registry.get("submissions", candidate["id"])

            def submit(identifier, parent_id=None):
                example = next(e for e in registry.examples if e["id"] == identifier)
                return wait(registry.submit({"trusted": True, "metadata": example["metadata"],
                                             "zip_base64": base64.b64encode(example["path"].read_bytes()).decode(),
                                             "parent_id": parent_id}))

            def passed(candidate):
                if candidate["status"] != "passed":
                    print(candidate["log"][-12000:])
                    raise AssertionError(f"{candidate['name']}@{candidate['version']}: {candidate['status']}: {candidate['checks']}")
                print(f"PASS: {candidate['name']}@{candidate['version']} with Stata {candidate['stata_version']}", flush=True)

            def approve(candidate):
                message = next(m for m in registry.demo_mailbox()["messages"] if m["submission_id"] == candidate["id"])
                registry.confirm(candidate["id"], {"code": message["code"]})
                before = registry.archive_state()
                accepted = registry.review(candidate["id"], {"decision": "approve", "reviewer": "Integration reviewer", "note": "Integration fixture checked."})
                assert accepted["delivery"]["status"] == "pending"
                assert registry.archive_state() == before
                delivered = registry.deliver(candidate["id"])
                assert delivered["delivery"]["status"] == "delivered", delivered["delivery"]
                assert registry.deliver(candidate["id"])["delivery"]["receipt"] == delivered["delivery"]["receipt"]

            def reproduce(candidate, name, dependencies=(), expect_pass=True):
                """Execute the downloaded recipe itself, independently of run_stata."""
                folder = Path(directory) / name
                folder.mkdir()
                source = registry.zip_for(candidate)
                kit = check_reproduction(source, candidate["metadata"])
                with zipfile.ZipFile(io.BytesIO(kit)) as archive:
                    # Both source fixtures and the generated kit are trusted here.
                    archive.extractall(folder)
                for path, content in bundle_files(source).items():
                    assert (folder / "package" / path).read_bytes() == content
                runner = folder / "reproduce.do"
                recipe = runner.read_text()
                for index, dependency in enumerate(dependencies, 1):
                    dependency_source = folder / "dependency-sources" / str(index)
                    unpack_bundle(registry.zip_for(dependency), dependency_source)
                    if not (dependency_source / "stata.toc").exists():
                        (dependency_source / "stata.toc").write_text(
                            f"v 3\nd Local reviewed dependency\np {dependency['name']} Local package\n")
                    # Follow the kit instructions: only fill in reviewed local sources.
                    recipe = recipe.replace(f"SET_DEPENDENCY_SOURCE_{index}", str(dependency_source))
                runner.write_text(recipe)
                # Follow the documented Stata-console step explicitly. macOS
                # Stata can restore its own working directory at startup.
                launch = folder / "launch.do"
                launch.write_text(f'cd "{folder.resolve()}"\ndo "{runner.resolve()}"\nexit, clear\n')
                (folder / "profile.do").write_text("* Empty local integration profile.\n")
                (folder / "tmp").mkdir()
                environment = {**os.environ, "S_ADO": "BASE", "STATATMP": str(folder / "tmp"),
                               "TERM": "xterm-256color"}
                master, slave = pty.openpty()
                try:
                    process = subprocess.Popen([registry.stata_path, "-q", "-e", "do", str(launch.resolve())],
                                               cwd=folder.resolve(), env=environment, stdin=slave,
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                               start_new_session=True)
                    try:
                        returncode = process.wait(timeout=60)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                        raise AssertionError("Generated reproduction recipe timed out.") from None
                finally:
                    os.close(master)
                    os.close(slave)
                log_path = folder / "SSCNG-CHECKS.log"
                assert log_path.exists(), "Generated reproduction recipe did not produce its log."
                log = log_path.read_text(errors="replace")
                complete = bool(re.search(r"(?m)^SSCNG_REPRODUCTION_COMPLETE\s*$", log))
                library = folder / "sscng-check-library" / "plus"
                installed = list(library.rglob(candidate["name"] + ".ado"))
                if expect_pass:
                    assert returncode == 0 and complete, log
                    assert len(installed) == 1, log
                    assert installed[0].read_bytes() == bundle_files(source)[candidate["name"] + ".ado"]
                    for dependency in dependencies:
                        installed_dependency = next(library.rglob(dependency["name"] + ".ado"))
                        assert installed_dependency.read_bytes() == bundle_files(
                            registry.zip_for(dependency))[dependency["name"] + ".ado"]
                else:
                    assert not complete and not installed, log
                    assert "SET_DEPENDENCY_SOURCE_1" in log, log
                print(f"PASS: Generated reproduction kit {name}", flush=True)

            baseline = submit("sscng-example-1.0.0")
            passed(baseline)
            reproduce(baseline, "without-dependencies")
            assert not registry.archive_state()["packages"]
            approve(baseline)
            current_file = registry.current_archive / "s/sscng_example.ado"
            baseline_bytes = bundle_files(registry.zip_for(baseline))["sscng_example.ado"]
            assert current_file.read_bytes() == baseline_bytes

            missing = submit("sscng-example-1.1.0")
            assert missing["status"] == "failed" and "dependency resolution" in missing["log"]
            assert current_file.read_bytes() == baseline_bytes
            print("PASS: Missing approved dependency blocks checks and leaves today's archive intact", flush=True)
            helper = submit("sscng-helper-0.1.0")
            passed(helper)
            approve(helper)
            update = submit("sscng-example-1.1.0", missing["id"])
            passed(update)
            assert update["dependency_releases"][0]["version"] == "0.1.0"
            registry.review(update["id"], {"decision": "request_changes", "reviewer": "Integration reviewer", "note": "Clarify the result change."})
            try:
                approve(update)
                raise AssertionError("A returned submission must not be approved.")
            except APIError:
                pass
            revision = wait(registry.submit({"trusted": True, "source_submission_id": update["id"],
                                             "parent_id": update["id"],
                                             "metadata": {**update["metadata"], "notes": "Result now doubles the input and adds one, using the approved helper."}}))
            passed(revision)
            reproduce(revision, "missing-dependency-source", expect_pass=False)
            reproduce(revision, "with-reviewed-dependency", dependencies=[helper])
            assert current_file.read_bytes() == baseline_bytes
            approve(revision)
            updated_files = bundle_files(registry.zip_for(revision))
            assert current_file.read_bytes() == updated_files["sscng_example.ado"]
            assert not (registry.current_archive / "s/smoke.do").exists()
            assert not (registry.current_archive / "s/metadata.json").exists()
            current = {item["name"]: item for item in registry.archive_state()["packages"]}
            assert current["sscng_example"]["submission_id"] == revision["id"]
            assert current["sscng_example"]["version"] == "1.1.0"
            assert len(current) == 2
            print("PASS: Reviewed revision replaces today's package with its exact installable files", flush=True)

            # Stata must install both current packages through the HTTP handoff.
            # First uninstall the runner's local copies so only HTTP installation
            # can make the subsequent example assertions succeed.
            files = dict(updated_files)
            url = f"http://127.0.0.1:{server.server_port}/archive/s/"
            files["smoke.do"] = ("ado uninstall sscng_example\nado uninstall sscng_helper\n" +
                                 f'net install sscng_helper, from("{url}")\n' +
                                 f'net install sscng_example, from("{url}")\n' +
                                 files["smoke.do"].decode()).encode()
            memory = io.BytesIO()
            with zipfile.ZipFile(memory, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
            dependencies = [(helper["metadata"], registry.zip_for(helper))]
            http_result = run_stata(memory.getvalue(), revision["metadata"], dependencies,
                                    Path(directory) / "http-check", registry.stata_path)
            assert http_result["status"] == "passed", http_result["log"]
            installed = next(Path(http_result["library_path"]).rglob("sscng_example.ado"))
            assert installed.read_bytes() == current_file.read_bytes()
            print("PASS: Real Stata net install from today's HTTP archive, including its dependency", flush=True)

            state_before = registry.archive_state()
            registry.close()
            reopened = Registry(directory)
            try:
                assert reopened.archive_state() == state_before
                assert current_file.read_bytes() == updated_files["sscng_example.ado"]
                assert reopened.get("submissions", revision["id"])["status"] == "approved"
                assert reopened.get("submissions", update["id"])["status"] == "changes_requested"
            finally:
                reopened.close()
            print("PASS: Current handoff and review evidence persist across restart", flush=True)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
            registry.close()


if __name__ == "__main__":
    main()
