#!/usr/bin/env python3
"""Opt-in integration check: executes only this repository's trusted fixtures.

Requires licensed local Stata. Uses a temporary registry and library, never the
normal Stata PLUS directory. Run from repository root: python3 tests/stata_integration.py
"""
import base64
import io
from pathlib import Path
import sys
import tempfile
import threading
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pilot.packages import bundle_files, run_stata
from pilot.service import Registry, make_server


def main():
    with tempfile.TemporaryDirectory(prefix="sscng-integration-") as directory:
        registry = Registry(directory)
        server = make_server(registry, 0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            def submit(identifier, parent_id=None):
                example = next(e for e in registry.examples if e["id"] == identifier)
                candidate = registry.submit({"trusted": True, "metadata": example["metadata"], "zip_base64": base64.b64encode(example["path"].read_bytes()).decode(), "parent_id": parent_id})
                registry.executor.submit(lambda: None).result(timeout=90)
                return registry.get("submissions", candidate["id"])

            def passed(candidate):
                if candidate["status"] != "passed":
                    print(candidate["log"][-12000:])
                    raise AssertionError(f"{candidate['name']}@{candidate['version']}: {candidate['status']}: {candidate['checks']}")
                print(f"PASS: {candidate['name']}@{candidate['version']} with Stata {candidate['stata_version']}", flush=True)

            def approve(candidate):
                registry.review(candidate["id"], {"decision": "approve", "note": "Integration fixture checked."})

            baseline = submit("sscng-example-1.0.0")
            passed(baseline)
            approve(baseline)
            missing = submit("sscng-example-1.1.0")
            assert missing["status"] == "failed" and "dependency resolution" in missing["log"]
            print("PASS: Missing approved dependency blocks execution and publication", flush=True)
            helper = submit("sscng-helper-0.1.0")
            passed(helper)
            approve(helper)
            update = submit("sscng-example-1.1.0", missing["id"])
            passed(update)
            approve(update)
            changed = registry.diff("sscng_example", "1.1.0")
            assert changed["previous_version"] == "1.0.0"
            assert "sscng_example.ado" in [item["path"] for item in changed["changed"]]
            restored = registry.restore("sscng_example", "1.0.0", {"trusted": True})
            assert restored["status"] == "passed", restored["log"]
            assert restored["environment"]["version"] == "1.0.0"
            assert list(Path(restored["library_path"]).rglob("sscng_example.ado"))
            print("PASS: Archived baseline installed and assertions passed in managed library", flush=True)

            # Verify Stata itself can consume HTTP .pkg and files from the API.
            files = bundle_files(registry.zip_for(baseline))
            url = f"http://127.0.0.1:{server.server_port}/packages/sscng_example/1.0.0/"
            files["smoke.do"] = (f'net install sscng_example, from("{url}") replace\n' + files["smoke.do"].decode()).encode()
            memory = io.BytesIO()
            with zipfile.ZipFile(memory, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
            http_result = run_stata(memory.getvalue(), baseline["metadata"], [], Path(directory) / "http-check", registry.stata_path)
            assert http_result["status"] == "passed", http_result["log"]
            print("PASS: Real Stata net install from the local HTTP archive", flush=True)
            registry.snapshot()
            assert len(registry.rows("releases")) == 3
            registry.close()
            reopened = Registry(directory)
            try:
                assert len(reopened.rows("releases")) == 3
                assert reopened.setting("environment")["version"] == "1.0.0"
            finally:
                reopened.close()
            print("PASS: Releases and restored environment survive reopening the database", flush=True)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
            registry.close()


if __name__ == "__main__":
    main()
