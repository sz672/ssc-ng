"""Safety and result-reporting checks; no installed Stata is needed."""

import copy
import hashlib
import io
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from pilot import packages


METADATA = {
    "name": "sample", "version": "1.0.0", "title": "Sample command",
    "maintainer": "Example Author", "email": "author@example.org", "stata": "16.0",
    "license": "MIT", "dependencies": [], "notes": "Initial package.",
}
FILES = {
    "sample.pkg": b"v 3\nd A sample\nf sample.ado\nf sample.sthlp\ne\n",
    "sample.ado": b"program define sample\nversion 16.0\nend\n",
    "sample.sthlp": b"{smcl}\nSample help\n",
    "smoke.do": b"sample\nassert 1 == 1\n",
}


def archive(files=None, wrapper=""):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for name, data in (FILES if files is None else files).items():
            output.writestr(wrapper + name, data)
    return stream.getvalue()


class ValidationTests(unittest.TestCase):
    def test_valid_bundle_has_exact_file_digests_and_wrapper_support(self):
        data = archive(wrapper="download/")
        result = packages.validate_bundle(data, METADATA)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual({item["path"] for item in result["files"]}, set(FILES))
        self.assertEqual(packages.bundle_files(data), FILES)

    def test_validation_does_not_execute_source(self):
        data = archive({**FILES, "smoke.do": b"exit 459\n"})
        with patch.object(packages.subprocess, "Popen", side_effect=AssertionError("executed")):
            self.assertTrue(packages.validate_bundle(data, METADATA)["passed"])

    def test_missing_inventory_member_fails(self):
        files = dict(FILES)
        del files["sample.ado"]
        result = packages.validate_bundle(archive(files), METADATA)
        self.assertFalse(result["passed"])
        self.assertIn("missing files", result["checks"][-1]["detail"])

    def test_required_inventory_help_and_smoke(self):
        for path in ("sample.pkg", "sample.sthlp", "smoke.do"):
            with self.subTest(path=path):
                files = dict(FILES)
                del files[path]
                self.assertFalse(packages.validate_bundle(archive(files), METADATA)["passed"])

    def test_manifest_requires_real_f_records_and_version(self):
        for content in (b"sample.ado\nsample.sthlp", b"v 2\nf sample.ado\n",
                        b"v 3\nf ../sample.ado\n", b"v 3\nf https://example.org/sample.ado\n",
                        b"v 3\ng MACARM64 sample.ado\n"):
            with self.subTest(content=content):
                self.assertFalse(packages.validate_bundle(archive({**FILES, "sample.pkg": content}), METADATA)["passed"])

    def test_manifest_supports_comments_tabs_and_optional_end(self):
        content = b"* comment\n\nv 3\nd\nf\tsample.ado\nf sample.sthlp\ne\nf ignored.txt\n"
        self.assertTrue(packages.validate_bundle(archive({**FILES, "sample.pkg": content}), METADATA)["passed"])

    def test_traversal_absolute_macro_and_windows_paths_fail(self):
        for path in ("../escaped", "/absolute", "C:/windows", "a\\b", "a/../b", "a/$x.ado"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    packages.bundle_files(archive({path: b"bad"}))

    def test_symlink_is_rejected(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as output:
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            output.writestr(info, "../outside")
        with self.assertRaisesRegex(ValueError, "Links"):
            packages.bundle_files(stream.getvalue())

    def test_case_collisions_and_file_directory_collisions_fail(self):
        for files in ({"a.ado": b"a", "A.ado": b"b"}, {"a": b"a", "a/b": b"b"}):
            with self.assertRaises(ValueError):
                packages.bundle_files(archive(files))

    def test_archive_file_count_and_expansion_limits(self):
        with self.assertRaises(ValueError):
            packages.bundle_files(archive({f"f{n}": b"" for n in range(201)}))
        with patch.object(packages, "MAX_EXTRACTED_BYTES", 100):
            with self.assertRaisesRegex(ValueError, "Expanded"):
                packages.bundle_files(archive({"large": b"x" * 101}))
        with patch.object(packages, "MAX_ZIP_BYTES", 10):
            with self.assertRaisesRegex(ValueError, "ZIP archive exceeds"):
                packages.bundle_files(archive())

    def test_invalid_metadata_and_dependency_versions_fail(self):
        for update in ({"name": "../sample"}, {"version": "1.0"}, {"email": "author"},
                       {"dependencies": [{"name": "helper", "version": ">=1.0.0"}]},
                       {"dependencies": [{"name": "sample", "version": "1.0.0"}]}):
            with self.subTest(update=update):
                self.assertFalse(packages.validate_bundle(archive(), {**METADATA, **update})["passed"])

    def test_extract_refuses_overwrite_and_destination_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "package"
            packages.unpack_bundle(archive(wrapper="wrapper/"), destination)
            self.assertEqual((destination / "sample.ado").read_bytes(), FILES["sample.ado"])
            with self.assertRaises(ValueError):
                packages.unpack_bundle(archive(), destination)
            link = Path(temporary) / "link"
            link.symlink_to(destination, target_is_directory=True)
            with self.assertRaises(ValueError):
                packages.unpack_bundle(archive(), link)


class RunnerTests(unittest.TestCase):
    def fake_process(self, mode="passed"):
        def start(command, **kwargs):
            runner = Path(command[-1]).read_text()
            token = re.search(r"SSCNG_[a-f0-9]+", runner).group()
            markers = re.findall(r'display "(' + token + r'_(?:MINIMUM|INSTALL_[0-9]+)=)"', runner)
            log = token + "_VERSION=19.5\n"
            if mode == "old_runtime":
                log = token + "_VERSION=15\n" + token + "_MINIMUM=9\n"
                markers = []
            elif mode == "no_log":
                log = ""
                markers = []
            log += "\n".join(marker + "0" for marker in markers) + "\n"
            if mode == "passed":
                log += token + "_SMOKE=0\n" + token + "_COMPLETE\n"
            elif mode == "failed":
                log += "assertion is false\nr(9);\n" + token + "_SMOKE=9\n"
            elif mode == "echo":
                log += f'. display "{token}_SMOKE=" 0\n. display "{token}_COMPLETE"\n'
            (Path(kwargs["cwd"]) / "checks.log").write_text(log)
            process = Mock(pid=12345)
            process.wait.return_value = 0
            if mode == "timeout":
                process.wait.side_effect = [subprocess.TimeoutExpired(command, 1), -9]
            return process
        return start

    def run_fake(self, directory, mode="passed"):
        with patch.object(packages.subprocess, "Popen", side_effect=self.fake_process(mode)):
            return packages.run_stata(archive(), copy.deepcopy(METADATA), [], Path(directory), sys.executable, timeout=1)

    def test_success_retains_an_isolated_library(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_fake(temporary)
            self.assertEqual(result["status"], "passed", result)
            library = Path(result["library_path"])
            self.assertTrue(library.is_dir())
            runner = (library.parent.parent / "runner.do").read_text()
            self.assertIn('global S_ADO "BASE;PLUS;PERSONAL;SITE"', runner)
            self.assertIn("net set ado PLUS", runner)
            self.assertIn("net install sample", runner)
            self.assertEqual(result["stata_version"], "19.5")

    def test_smoke_error_is_failure_even_if_process_exits_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_fake(temporary, "failed")
            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("r(9)" in check["detail"] for check in result["checks"]))

    def test_echoed_commands_cannot_count_as_completion(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(self.run_fake(temporary, "echo")["status"], "failed")

    def test_timeout_kills_process_group_and_never_passes(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(packages.os, "killpg") as kill:
            result = self.run_fake(temporary, "timeout")
            self.assertEqual(result["status"], "failed")
            kill.assert_called_once_with(12345, packages.signal.SIGKILL)
            self.assertIn("time limit", result["checks"][-1]["detail"])

    def test_missing_runtime_is_unavailable_without_executing(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(packages.subprocess, "Popen") as start:
            result = packages.run_stata(archive(), METADATA, [], Path(temporary), "/definitely/missing/stata")
            self.assertEqual(result["status"], "unavailable")
            start.assert_not_called()

    def test_runtime_older_than_required_cannot_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_fake(temporary, "old_runtime")
            self.assertEqual(result["status"], "failed")
            minimum = next(check for check in result["checks"] if check["name"] == "Minimum Stata version")
            self.assertEqual(minimum["status"], "failed")
            runner = next(Path(temporary).glob("stata-*/runner.do")).read_text()
            self.assertLess(runner.index("assert c(stata_version) >= 16"), runner.index("net install"))

    def test_startup_without_a_job_log_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(self.run_fake(temporary, "no_log")["status"], "unavailable")

    def test_private_terminal_descriptors_are_closed_on_finish_and_launch_error(self):
        for launch_fails in (False, True):
            with self.subTest(launch_fails=launch_fails), tempfile.TemporaryDirectory() as temporary:
                descriptors = os.openpty()
                with patch.object(packages.pty, "openpty", return_value=descriptors):
                    if launch_fails:
                        with patch.object(packages.subprocess, "Popen", side_effect=OSError("cannot start")):
                            result = packages.run_stata(archive(), METADATA, [], Path(temporary), sys.executable)
                        self.assertEqual(result["status"], "unavailable")
                    else:
                        result = self.run_fake(temporary)
                        self.assertEqual(result["status"], "passed")
                for descriptor in descriptors:
                    with self.assertRaises(OSError):
                        os.fstat(descriptor)

    def test_dependency_minimum_version_is_checked_before_any_install(self):
        dependency_metadata = {**METADATA, "name": "helper", "stata": "19.5"}
        dependency_files = {name.replace("sample", "helper"): data.replace(b"sample", b"helper")
                            for name, data in FILES.items()}
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(packages.subprocess, "Popen", side_effect=self.fake_process()):
                result = packages.run_stata(archive(), METADATA,
                                            [(dependency_metadata, archive(dependency_files))],
                                            Path(temporary), sys.executable)
            self.assertEqual(result["status"], "passed", result)
            runner = next(Path(temporary).glob("stata-*/runner.do")).read_text()
            self.assertLess(runner.index("assert c(stata_version) >= 19.5"), runner.index("net install helper"))
            self.assertLess(runner.index("net install helper"), runner.index("net install sample"))


if __name__ == "__main__":
    unittest.main()
