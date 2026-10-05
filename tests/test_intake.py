"""Author-facing import and reproduction keep files bounded and declarations explicit."""

import io
import json
import unittest
from unittest.mock import patch
import zipfile

from pilot import intake, packages


METADATA = {"name": "sample", "version": "1.0.0", "title": "Sample command",
            "maintainer": "Example Author", "email": "author@example.org", "stata": "16.0",
            "license": "MIT", "dependencies": [], "notes": "Initial submission."}
FILES = {"sample.pkg": b"v 3\nd Title: Example title\nd Version: 1.0.0\nd Stata: 16.0\nf sample.ado\nf sample.sthlp\n",
         "sample.ado": b"program sample\nend\n", "sample.sthlp": b"{smcl}\nSample help\n",
         "smoke.do": b"sample\n"}


def bundle(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    return output.getvalue()


class IntakeTests(unittest.TestCase):
    def test_import_preserves_declared_values_and_discards_unknown_fields(self):
        declaration = {**METADATA, "title": "Declared title", "admin": True,
                       "dependencies": [{"name": "helper", "version": "1.2.3", "command": "ignored"}]}
        result = intake.inspect_upload(bundle({**FILES, "metadata.json": json.dumps(declaration)}))
        self.assertEqual(result["metadata"]["title"], "Declared title")
        self.assertEqual(result["sources"]["title"], "metadata.json")
        self.assertNotIn("admin", result["metadata"])
        self.assertEqual(result["metadata"]["dependencies"], [{"name": "helper", "version": "1.2.3"}])
        self.assertEqual(result["missing_fields"], [])
        self.assertEqual(result["suggested_name"], "sample")

    def test_missing_metadata_imports_only_explicit_unambiguous_headers(self):
        result = intake.inspect_upload(bundle(FILES))
        self.assertEqual(result["metadata"], {"name": "sample", "title": "Example title", "version": "1.0.0", "stata": "16.0"})
        self.assertIn("email", result["missing_fields"])
        ambiguous = {**FILES, "sample.pkg": FILES["sample.pkg"] + b"d Version: 2.0.0\n"}
        self.assertNotIn("version", intake.inspect_upload(bundle(ambiguous))["metadata"])
        another = {**FILES, "other.pkg": FILES["sample.pkg"]}
        result = intake.inspect_upload(bundle(another))
        self.assertEqual(result["metadata"], {})
        self.assertIsNone(result["suggested_name"])
        self.assertEqual(result["checks"][-1]["status"], "failed")

    def test_malformed_metadata_still_returns_inventory_and_actionable_diagnostics(self):
        for malformed in (b"[]", b"not json", b"\xff", b'{"title": []}', b'{"dependencies": "none"}'):
            with self.subTest(malformed=malformed):
                result = intake.inspect_upload(bundle({**FILES, "metadata.json": malformed}))
                self.assertEqual(len(result["inventory"]), 5)
                failure = next(check for check in result["checks"] if check["code"] == "metadata_import")
                self.assertEqual(failure["status"], "failed")
                self.assertEqual(failure["severity"], "error")
                self.assertTrue(failure["remedy"])
                self.assertEqual(result["metadata"]["name"], "sample")

    def test_import_does_not_execute_and_obeys_archive_and_metadata_limits(self):
        with patch.object(packages.subprocess, "Popen", side_effect=AssertionError("executed")):
            self.assertTrue(intake.inspect_upload(bundle(FILES))["inventory"])
        with patch.object(packages, "MAX_EXTRACTED_BYTES", 5):
            result = intake.inspect_upload(bundle(FILES))
        self.assertFalse(result["inventory"])
        self.assertEqual(result["checks"][0]["status"], "failed")
        result = intake.inspect_upload(bundle({"../outside": b"x"}))
        self.assertFalse(result["inventory"])
        result = intake.inspect_upload(bundle({**FILES, "metadata.json": b" " * (intake.MAX_METADATA_BYTES + 1)}))
        self.assertTrue(any("64 KiB" in check["detail"] for check in result["checks"]))

    def test_reproduction_keeps_exact_files_and_uses_a_clean_library_and_selected_test(self):
        files = {**FILES, "tests/check.do": b"sample\nassert 1 == 1\n"}
        metadata = {**METADATA, "test_file": "tests/check.do",
                    "dependencies": [{"name": "helper", "version": "1.2.3"}]}
        with patch.object(packages.subprocess, "Popen", side_effect=AssertionError("executed")):
            output = intake.check_reproduction(bundle(files), metadata)
        with zipfile.ZipFile(io.BytesIO(output)) as archive:
            for path, content in files.items():
                self.assertEqual(archive.read("package/" + path), content)
            instructions = archive.read("SSCNG-CHECKS.txt").decode()
            runner = archive.read("reproduce.do").decode()
        self.assertIn("helper 1.2.3", instructions)
        self.assertIn("not included", instructions)
        self.assertIn('do "reproduce.do"', instructions)
        self.assertIn('global S_ADO "BASE;PLUS;PERSONAL;SITE"', runner)
        self.assertIn('mkdir "sscng-check-library"', runner)
        self.assertIn('do "tests/check.do"', runner)
        self.assertLess(runner.index("net install helper"), runner.index("net install sample"))
        self.assertIn("SET_DEPENDENCY_SOURCE_1", runner)

    def test_reproduction_cannot_generate_commands_from_unsafe_metadata(self):
        for invalid in ({"name": 'sample"'}, {"test_file": "../outside.do"}, {"stata": "16\nexit"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                intake.check_reproduction(bundle(FILES), {**METADATA, **invalid})


if __name__ == "__main__":
    unittest.main()
