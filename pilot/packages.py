"""Bounded ZIP validation and a local Stata runner for trusted pilot packages.

Validation never executes submitted code. Running Stata does: separate directories
and an isolated ado-path are not an operating-system sandbox. Only use the runner
with packages the local operator trusts. No global Stata installation is changed.
"""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import pty
import re
import shutil
import signal
import stat
import subprocess
import uuid
import zipfile
import zlib


MAX_ZIP_BYTES = 10 * 1024 * 1024
MAX_EXTRACTED_BYTES = 30 * 1024 * 1024
MAX_FILES = 200
MAX_LOG_BYTES = 1024 * 1024
NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
VERSION_RE = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")


def _check(name: str, passed: bool, detail: str) -> dict:
    return {"name": name, "status": "passed" if passed else "failed", "detail": detail}


def _safe_path(value: str) -> str:
    # A deliberately small portable filename alphabet also prevents Stata macro,
    # URL, Windows drive, and quoted-command interpretation of manifest paths.
    if not value or len(value) > 240 or "\\" in value or value.startswith("/"):
        raise ValueError(f"Unsafe package path: {value!r}")
    parts = value.split("/")
    if any(p in ("", ".", "..") or p.endswith(".") or
           not re.fullmatch(r"[A-Za-z0-9_.-]+", p) for p in parts):
        raise ValueError(f"Unsafe package path: {value!r}")
    return value


def bundle_files(zip_bytes: bytes) -> dict[str, bytes]:
    """Read bounded, safe regular files; remove one common wrapper directory.

    Raises ValueError for invalid/unsafe archives. Paths returned are canonical
    package-relative paths, identical to those returned by validate_bundle.
    """
    if not isinstance(zip_bytes, bytes) or not zip_bytes:
        raise ValueError("Supply a nonempty ZIP archive.")
    if len(zip_bytes) > MAX_ZIP_BYTES:
        raise ValueError("ZIP archive exceeds the 10 MiB limit.")
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_FILES * 2:
                raise ValueError("ZIP archive has too many directory/file entries.")
            seen: set[str] = set()
            file_entries = []
            declared_size = 0
            for entry in entries:
                if entry.orig_filename != entry.filename:
                    raise ValueError("ZIP filenames must not contain NUL bytes.")
                name = _safe_path(entry.filename[:-1] if entry.is_dir() else entry.filename)
                mode = (entry.external_attr >> 16) & 0xFFFF
                kind = stat.S_IFMT(mode)
                if kind not in (0, stat.S_IFDIR if entry.is_dir() else stat.S_IFREG):
                    raise ValueError(f"Links and special files are not allowed: {name}")
                if name.casefold() in seen:
                    raise ValueError(f"Duplicate or case-colliding ZIP path: {name}")
                seen.add(name.casefold())
                if entry.flag_bits & 1:
                    raise ValueError("Encrypted ZIP entries are not supported.")
                if entry.is_dir():
                    continue
                declared_size += entry.file_size
                if declared_size > MAX_EXTRACTED_BYTES:
                    raise ValueError("Expanded ZIP archive exceeds the 30 MiB limit.")
                file_entries.append((name, entry))
            if not file_entries or len(file_entries) > MAX_FILES:
                raise ValueError("ZIP archive must contain between 1 and 200 files.")
            names = {name.casefold() for name, _ in file_entries}
            for name, _ in file_entries:
                if any(str(parent).casefold() in names for parent in PurePosixPath(name).parents
                       if str(parent) != "."):
                    raise ValueError(f"A file conflicts with a directory: {name}")
            # Only remove a wrapper when every regular file is inside it.
            first_parts = {name.split("/")[0] for name, _ in file_entries}
            strip_wrapper = len(first_parts) == 1 and all("/" in name for name, _ in file_entries)
            files = {}
            actual_size = 0
            for name, entry in file_entries:
                with archive.open(entry) as stream:
                    data = stream.read(MAX_EXTRACTED_BYTES - actual_size + 1)
                actual_size += len(data)
                if actual_size > MAX_EXTRACTED_BYTES or len(data) != entry.file_size:
                    raise ValueError("Expanded ZIP content exceeds its allowed size.")
                canonical = name.split("/", 1)[1] if strip_wrapper else name
                files[canonical] = data
            return dict(sorted(files.items()))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, RuntimeError,
            EOFError, OSError, zlib.error) as exc:
        raise ValueError(f"Cannot read ZIP archive: {exc}") from exc


def _file_records(files: dict[str, bytes]) -> list[dict]:
    return [{"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in files.items()]


def _metadata_error(metadata: dict) -> str | None:
    if not isinstance(metadata, dict):
        return "Metadata must be an object."
    if not isinstance(metadata.get("name"), str) or not NAME_RE.fullmatch(metadata["name"]):
        return "Package name must start with a lowercase letter and use up to 32 lowercase letters, digits, or underscores."
    if not isinstance(metadata.get("version"), str) or not VERSION_RE.fullmatch(metadata["version"]):
        return "Package version must be X.Y.Z, with no leading zeros."
    for key, limit in (("title", 200), ("maintainer", 200), ("email", 254),
                       ("stata", 10), ("license", 100), ("notes", 10000)):
        value = metadata.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
            return f"Provide {key} as nonempty text of at most {limit} characters."
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", metadata["email"]):
        return "Provide a valid maintainer email address."
    if not re.fullmatch(r"[0-9]{1,2}(?:\.[0-9]{1,2})?", metadata["stata"]):
        return "Minimum Stata version must be numeric, for example 16.0."
    dependencies = metadata.get("dependencies")
    if not isinstance(dependencies, list) or len(dependencies) > 50:
        return "Dependencies must be a list of at most 50 exact name/version records."
    names = set()
    for dependency in dependencies:
        if not isinstance(dependency, dict) or not isinstance(dependency.get("name"), str) or not NAME_RE.fullmatch(dependency["name"]):
            return "Each dependency needs a valid package name."
        if not isinstance(dependency.get("version"), str) or not VERSION_RE.fullmatch(dependency["version"]):
            return "Each dependency needs an exact X.Y.Z version."
        name = dependency["name"]
        if name == metadata["name"] or name in names:
            return "Dependencies must not repeat a name or include the package itself."
        names.add(name)
    return None


def _manifest_files(content: bytes) -> list[str]:
    if len(content) > 1024 * 1024:
        raise ValueError("Package inventory exceeds 1 MiB.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Package inventory must be UTF-8 text.") from exc
    version_seen = False
    files = []
    seen = set()
    for line_number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("*"):
            continue
        command, _, argument = line.partition(" ")
        if "\t" in command:
            command, argument = line.split(None, 1)
        argument = argument.strip()
        if not version_seen:
            if command != "v" or argument != "3":
                raise ValueError("The first inventory record must be 'v 3'.")
            version_seen = True
            continue
        if command == "e":
            break  # Stata's optional end-of-input record.
        if command == "d":
            continue
        if command != "f":
            raise ValueError(f"Unsupported inventory record on line {line_number}; this pilot supports v 3, d, f, and e.")
        name = _safe_path(argument)
        if name.casefold() in seen:
            raise ValueError(f"Inventory lists the same file more than once: {name}")
        seen.add(name.casefold())
        files.append(name)
    if not version_seen or not files:
        raise ValueError("Package inventory needs 'v 3' and at least one 'f filename' record.")
    return files


def validate_bundle(zip_bytes: bytes, metadata: dict) -> dict:
    """Validate metadata and package inventory without executing source files."""
    checks = []
    result = {"checks": checks, "files": [],
              "sha256": hashlib.sha256(zip_bytes).hexdigest() if isinstance(zip_bytes, bytes) else "",
              "passed": False}
    metadata_error = _metadata_error(metadata)
    checks.append(_check("Metadata", metadata_error is None, metadata_error or "Required metadata and exact dependency versions are valid."))
    try:
        files = bundle_files(zip_bytes)
    except ValueError as exc:
        checks.append(_check("ZIP safety", False, str(exc)))
        return result
    result["files"] = _file_records(files)
    checks.append(_check("ZIP safety", True, f"{len(files)} safe files; archive and expanded sizes are within limits."))
    if metadata_error:
        return result
    inventory = metadata["name"] + ".pkg"
    try:
        if inventory not in files:
            raise ValueError(f"Missing root package inventory: {inventory}")
        listed = _manifest_files(files[inventory])
        missing = [name for name in listed if name not in files]
        if missing:
            raise ValueError("Inventory references missing files: " + ", ".join(missing))
        if not any(name.endswith(".ado") for name in listed):
            raise ValueError("Inventory must list at least one .ado source file.")
        if not any(name.endswith(".sthlp") for name in listed):
            raise ValueError("Inventory must list at least one .sthlp help file.")
        if "smoke.do" not in files:
            raise ValueError("Include a smoke.do example at the package root.")
    except ValueError as exc:
        checks.append(_check("Package structure", False, str(exc)))
        return result
    checks.append(_check("Package structure", True, f"{inventory} lists {len(listed)} existing files; ado, help, and smoke.do are present."))
    result["passed"] = True
    return result


def unpack_bundle(zip_bytes: bytes, dest: Path) -> list[dict]:
    """Extract canonical regular files, refusing symlinks and overwrites."""
    files = bundle_files(zip_bytes)
    dest = Path(dest)
    if dest.is_symlink():
        raise ValueError("Extraction destination must not be a symlink.")
    dest.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        target = dest.joinpath(*name.split("/"))
        parent = dest
        for component in name.split("/")[:-1]:
            parent = parent / component
            if parent.is_symlink():
                raise ValueError("Extraction destination contains a symlink.")
            parent.mkdir(exist_ok=True)
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
        except OSError as exc:
            raise ValueError(f"Cannot safely extract {name}: {exc.strerror}") from exc
    return _file_records(files)


def discover_stata() -> str | None:
    """Find a local executable without starting Stata or reading its license."""
    configured = os.environ.get("SSCNG_STATA")
    if configured:
        candidate = Path(configured).expanduser()
        return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None
    candidates = [Path("/Applications/StataNow/StataBE.app/Contents/MacOS/StataBE")]
    for directory in ("StataNow", "Stata"):
        for edition in ("StataMP", "StataSE", "StataBE", "Stata"):
            candidates.append(Path("/Applications") / directory / (edition + ".app") / "Contents/MacOS" / edition)
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return next((found for name in ("stata-mp", "stata-se", "stata") if (found := shutil.which(name))), None)


def _stata_path(path: Path) -> str:
    value = str(path.resolve())
    if any(character in value for character in ('"', "`", "'", "$", "\n", "\r")):
        raise ValueError("Stata job paths must not contain quotes, macro characters, or newlines.")
    return '"' + value + '"'


def _read_log(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            content = stream.read(MAX_LOG_BYTES + 1)
        return content[:MAX_LOG_BYTES].decode("utf-8", errors="replace") + ("\n[Log truncated at 1 MiB.]\n" if len(content) > MAX_LOG_BYTES else "")
    except OSError:
        return ""


def run_stata(zip_bytes: bytes, metadata: dict,
              dependency_bundles: list[tuple[dict, bytes]], work_dir: Path,
              stata_path: str | None, timeout: int = 60) -> dict:
    """Install trusted packages into a fresh library and execute smoke.do.

    The process uses official BASE plus job-local PLUS/PERSONAL/SITE only.
    Jobs and installed libraries are retained. An unavailable runtime never
    reports a package as passing. Runtime startup output is deliberately omitted
    from returned logs; the job log starts after initialization.
    """
    result = {"status": "failed", "checks": [], "log": "", "stata_version": None,
              "library_path": None}
    candidate = validate_bundle(zip_bytes, metadata)
    if not candidate["passed"]:
        result["checks"] = candidate["checks"]
        return result
    packages = list(dependency_bundles) + [(metadata, zip_bytes)]
    seen = set()
    for package_metadata, data in packages:
        validation = validate_bundle(data, package_metadata)
        if not validation["passed"]:
            result["checks"].append(_check("Dependency validation", False, "; ".join(check["detail"] for check in validation["checks"] if check["status"] == "failed")))
            return result
        if package_metadata["name"] in seen:
            result["checks"].append(_check("Dependency validation", False, "A dependency or candidate package is listed more than once."))
            return result
        seen.add(package_metadata["name"])
    executable = stata_path or discover_stata()
    if not executable or not Path(executable).is_file() or not os.access(executable, os.X_OK):
        result["status"] = "unavailable"
        result["checks"].append(_check("Stata runtime", False, "Stata is unavailable. Set SSCNG_STATA to an executable and rerun checks."))
        return result
    if timeout <= 0 or timeout > 3600:
        raise ValueError("Stata timeout must be between 1 and 3600 seconds.")
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    job = work_dir / ("stata-" + uuid.uuid4().hex)
    job.mkdir(mode=0o700)
    library = job / "lib" / "plus"
    for directory in (library, job / "lib/personal", job / "lib/site", job / "lib/oldplace", job / "tmp"):
        directory.mkdir(parents=True)
    result["library_path"] = str(library)
    # A local empty profile shadows the usual per-user profile lookup. Clear all
    # also unloads programs before setting the explicit ado search path below.
    (job / "profile.do").write_text("* SSC-NG local job: no user profile commands.\n", encoding="utf-8")
    token = "SSCNG_" + uuid.uuid4().hex
    log_path = job / "checks.log"
    lines = ["clear all", "set more off", "set linesize 255", "capture log close _all",
             f"log using {_stata_path(log_path)}, text replace name(sscng)",
             f'display "{token}_VERSION=" c(stata_version)',
             f"sysdir set PLUS {_stata_path(library)}",
             f"sysdir set PERSONAL {_stata_path(job / 'lib/personal')}",
             f"sysdir set SITE {_stata_path(job / 'lib/site')}",
             f"sysdir set OLDPLACE {_stata_path(job / 'lib/oldplace')}",
             'global S_ADO "BASE;PLUS;PERSONAL;SITE"',
             "net set ado PLUS", f"net set other {_stata_path(job / 'tmp')}",
             "adopath"]
    minimum_stata = max(float(package_metadata["stata"]) for package_metadata, _ in packages)
    minimum_marker = token + "_MINIMUM="
    expected = [(minimum_marker, "Minimum Stata version")]
    lines += [f"capture noisily assert c(stata_version) >= {minimum_stata:g}",
              "local sscng_rc = _rc", f'display "{minimum_marker}" `sscng_rc\'',
              "if `sscng_rc' != 0 exit `sscng_rc'"]
    for index, (package_metadata, data) in enumerate(packages):
        source = job / "source" / str(index)
        unpack_bundle(data, source)
        # A local net source needs a table of contents on some Stata versions.
        (source / "stata.toc").write_text(f"v 3\nd SSC-NG local test source\np {package_metadata['name']} Local package\n", encoding="utf-8")
        marker = f"{token}_INSTALL_{index}="
        expected.append((marker, "Install " + package_metadata["name"]))
        lines += [f"capture noisily net install {package_metadata['name']}, from({_stata_path(source)})",
                  "local sscng_rc = _rc", f'display "{marker}" `sscng_rc\'',
                  "if `sscng_rc' != 0 exit `sscng_rc'"]
    marker = token + "_SMOKE="
    expected.append((marker, "Example execution"))
    candidate_source = job / "source" / str(len(packages) - 1)
    lines += [f"cd {_stata_path(candidate_source)}",
              'global S_ADO "BASE;PLUS;PERSONAL;SITE"',
              f"capture noisily do {_stata_path(candidate_source / 'smoke.do')}",
              "local sscng_rc = _rc", f'display "{marker}" `sscng_rc\'',
              "if `sscng_rc' != 0 exit `sscng_rc'", f'display "{token}_COMPLETE"',
              "log close sscng", "exit, clear"]
    runner = job / "runner.do"
    runner.write_text("\n".join(lines) + "\n", encoding="utf-8")
    environment = os.environ.copy()
    environment["S_ADO"] = "BASE"
    environment["STATATMP"] = str(job / "tmp")
    # Mac Stata can silently ignore batch input when launched by a background
    # HTTP service without a terminal. Supply a private pseudo-terminal; all
    # reportable output still goes only to our post-initialization checks.log.
    try:
        terminal_master, terminal_slave = pty.openpty()
    except OSError:
        result["status"] = "unavailable"
        result["checks"].append(_check("Stata runtime", False, "A private terminal could not be allocated for Stata."))
        return result
    environment["TERM"] = "xterm-256color"
    try:
        process = subprocess.Popen([str(Path(executable).resolve()), "-q", "-e", "do", str(runner)],
                                   cwd=job, env=environment, stdin=terminal_slave,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
    except OSError:
        os.close(terminal_master)
        os.close(terminal_slave)
        result["status"] = "unavailable"
        result["checks"].append(_check("Stata runtime", False, "Stata could not be started; check its executable and local installation."))
        return result
    timed_out = False
    try:
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            returncode = process.wait()
    finally:
        os.close(terminal_master)
        os.close(terminal_slave)
    log = _read_log(log_path)
    result["log"] = log
    version = re.search(r"(?m)^" + re.escape(token) + r"_VERSION=\s*([0-9]+(?:\.[0-9]+)?)\s*$", log)
    if version:
        result["stata_version"] = version.group(1)
    result["checks"].append(_check("Stata runtime", bool(version),
                                 f"Stata {version.group(1)} started with an isolated package library." if version else "Stata did not produce a usable job log; inspect the local installation."))
    for marker, name in expected:
        match = re.search(r"(?m)^" + re.escape(marker) + r"\s*([0-9]+)\s*$", log)
        passed = bool(match and match.group(1) == "0")
        detail = ("Completed successfully." if passed else f"Stata returned r({match.group(1)})." if match else "Did not complete; an earlier check failed or the job stopped.")
        result["checks"].append(_check(name, passed, detail))
    complete = bool(re.search(r"(?m)^" + re.escape(token) + r"_COMPLETE\s*$", log))
    result["checks"].append(_check("Job completion", not timed_out and returncode == 0 and complete,
                                 f"Stopped after the {timeout}-second time limit." if timed_out else
                                 "Stata completed the installation and example checks." if returncode == 0 and complete else
                                 f"Stata did not finish all checks (process exit {returncode})."))
    if all(check["status"] == "passed" for check in result["checks"]):
        result["status"] = "passed"
    elif version is None:
        result["status"] = "unavailable"
    return result
