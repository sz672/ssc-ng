"""Read-only submission import and portable instructions for reproducing checks."""

from __future__ import annotations

import io
import json
from pathlib import PurePosixPath
import re
import zipfile

from .packages import (NAME_RE, VERSION_RE, _check, _file_records,
                       _manifest_hints, _metadata_error, bundle_files)


REQUIRED_FIELDS = ("name", "version", "title", "maintainer", "email", "stata",
                   "license", "notes", "dependencies")
TEXT_LIMITS = {"name": 32, "version": 100, "title": 200, "maintainer": 200,
               "email": 254, "stata": 10, "license": 100, "notes": 10000,
               "test_file": 240, "source_url": 2000}
MAX_METADATA_BYTES = 64 * 1024


def inspect_upload(zip_bytes: bytes) -> dict:
    """Inspect bounded files without running code or treating metadata as commands.

    Return only supported metadata fields. Sources let the form distinguish
    declarations in metadata.json from conservative hints in a root inventory.
    The submission form remains responsible for deliberate user overrides.
    """
    result = {"metadata": {}, "inventory": [], "checks": [], "suggested_name": None,
              "missing_fields": list(REQUIRED_FIELDS), "sources": {}}
    checks = result["checks"]
    try:
        files = bundle_files(zip_bytes)
    except ValueError as exc:
        checks.append(_check("ZIP safety", False, str(exc)))
        return result
    result["inventory"] = _file_records(files)
    checks.append(_check("ZIP safety", True, f"Read {len(files)} safe files without executing code."))
    metadata = result["metadata"]
    if "metadata.json" in files:
        try:
            if len(files["metadata.json"]) > MAX_METADATA_BYTES:
                raise ValueError("metadata.json exceeds 64 KiB.")
            declared = json.loads(files["metadata.json"].decode("utf-8-sig"))
            if not isinstance(declared, dict):
                raise ValueError("metadata.json must contain a JSON object.")
            invalid = []
            for field, limit in TEXT_LIMITS.items():
                if field not in declared:
                    continue
                value = declared[field]
                if not isinstance(value, str) or len(value) > limit or "\x00" in value:
                    invalid.append(field)
                    continue
                metadata[field] = value.strip()
            if "dependencies" in declared:
                dependencies = declared["dependencies"]
                if (isinstance(dependencies, list) and len(dependencies) <= 50 and
                    all(isinstance(item, dict) and isinstance(item.get("name"), str) and
                        isinstance(item.get("version"), str) and
                        NAME_RE.fullmatch(item["name"]) and VERSION_RE.fullmatch(item["version"])
                        for item in dependencies)):
                    metadata["dependencies"] = [{"name": item["name"], "version": item["version"]}
                                                for item in dependencies]
                else:
                    invalid.append("dependencies")
            result["sources"].update({field: "metadata.json" for field in metadata})
            checks.append(_check("Metadata import", not invalid,
                                 "Imported supported fields from metadata.json; review the form before submitting."
                                 if not invalid else "Could not import invalid fields: " + ", ".join(invalid) + ".",
                                 code="metadata_import",
                                 remedy="Correct these fields in metadata.json or enter them in the form; dependencies need exact name/version records."))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
            checks.append(_check("Metadata import", False, str(exc), code="metadata_import",
                                 remedy="Save metadata.json as a UTF-8 JSON object under 64 KiB, or remove it and complete the form manually."))
    else:
        checks.append(_check("Metadata import", True, "No metadata.json was supplied. Complete the fields not provided by explicit inventory headers."))

    inventories = [path for path in files if "/" not in path and path.endswith(".pkg")]
    if len(inventories) == 1:
        inventory = inventories[0]
        suggested = PurePosixPath(inventory).stem
        if NAME_RE.fullmatch(suggested):
            result["suggested_name"] = suggested
            if not metadata.get("name"):
                metadata["name"] = suggested
                result["sources"]["name"] = inventory
        for field, value in _manifest_hints(files[inventory]).items():
            valid = ((field == "version" and VERSION_RE.fullmatch(value)) or
                     (field == "stata" and re.fullmatch(r"[0-9]{1,2}(?:\.[0-9]{1,2})?", value)) or
                     (field == "title" and len(value) <= TEXT_LIMITS["title"]))
            if valid and not metadata.get(field):
                metadata[field] = value
                result["sources"][field] = inventory
        checks.append(_check("Inventory import", True, f"Found {inventory}; only explicit, unambiguous headers were used as hints."))
    else:
        checks.append(_check("Inventory import", False,
                             "No root .pkg inventory was found." if not inventories else "More than one root .pkg inventory was found; package identity is ambiguous.",
                             remedy="Include the intended package's name.pkg at the ZIP root, or inside one shared wrapper folder."))
    result["missing_fields"] = [field for field in REQUIRED_FIELDS
                                if field not in metadata or metadata[field] in (None, "")]
    return result


def check_reproduction(zip_bytes: bytes, metadata: dict) -> bytes:
    """Package the exact submitted files and a local Stata recipe; execute nothing.

    Dependencies are intentionally explicit local sources, never fetched from a
    moving online repository. The author must fill in each source path before
    running the recipe. The fresh library prevents globally installed commands
    from hiding missing dependencies.
    """
    files = bundle_files(zip_bytes)
    error = _metadata_error(metadata)
    if error:
        raise ValueError(error)
    test_file = metadata.get("test_file", "smoke.do")
    dependencies = metadata["dependencies"]
    text = [
        "SSC-NG: reproduce this submission's Stata checks",
        "================================================",
        f"Candidate: {metadata['name']} {metadata['version']}",
        f"Minimum Stata: {metadata['stata']}",
        f"Executable test: package/{test_file}",
        "",
        "The package/ directory contains the submitted files with identical contents.",
        "The download does not run them. Review the code before running trusted submissions.",
        "The recipe isolates Stata's package library; it is not an OS security sandbox.",
        "",
        "1. Extract this ZIP to a NEW temporary directory. Do not reuse a check library.",
        "   Use a path without quote, dollar, or backtick characters.",
        "2. Use a licensed Stata installation meeting the candidate's minimum version",
        "   AND every dependency's minimum version. The server report records its runtime.",
        "3. If dependencies are listed below, obtain those exact reviewed source bundles",
        "   and their transitive dependencies. Unpack each locally. Edit reproduce.do",
        "   to replace SET_DEPENDENCY_SOURCE paths and add transitive dependencies first.",
        "   Each source must contain its .pkg, listed files, and a valid stata.toc.",
        "   Dependencies are not included in this download and are never fetched automatically.",
        "4. In Stata, change directory to this extracted folder, then run:",
        '     do "reproduce.do"',
        "5. Inspect SSCNG-CHECKS.log at the FIRST error. A completed run prints",
        "   SSCNG_REPRODUCTION_COMPLETE. Fix the package/test and submit a revision.",
        "",
        "Declared direct dependencies (exact versions):",
    ]
    text.extend([f"  {item['name']} {item['version']}" for item in dependencies] or ["  None."])
    text += ["", "The recipe creates sscng-check-library with fresh PLUS/PERSONAL/SITE directories.",
             "If the directory already exists, the recipe stops. Extract a fresh copy to rerun.",
             "It changes no global Stata library. Check logs stay beside reproduce.do.",
             "The local recipe helps diagnose failures; the submission must still pass server checks.", ""]
    lines = [
        "* Generated SSC-NG local reproduction recipe. Review package code first.",
        "clear all", "set more off", "set linesize 255", "capture log close _all",
        'log using "SSCNG-CHECKS.log", text replace',
        'display "Stata runtime: " c(stata_version)',
        f"assert c(stata_version) >= {metadata['stata']}",
        "local sscng_root = c(pwd)",
        '* Stop if the check library already exists; each run must start clean.',
        'mkdir "sscng-check-library"',
    ]
    for directory in ("plus", "personal", "site", "oldplace", "other"):
        lines.append(f'mkdir "sscng-check-library/{directory}"')
    for directory in ("PLUS", "PERSONAL", "SITE", "OLDPLACE"):
        lines.append(f'sysdir set {directory} "`sscng_root\'/sscng-check-library/{directory.lower()}"')
    lines += ['global S_ADO "BASE;PLUS;PERSONAL;SITE"', "net set ado PLUS",
              'net set other "`sscng_root\'/sscng-check-library/other"', "adopath"]
    for index, dependency in enumerate(dependencies, 1):
        lines += [f"* Required: {dependency['name']} {dependency['version']}. Set this reviewed local source.",
                  f'net install {dependency["name"]}, from("SET_DEPENDENCY_SOURCE_{index}")']
    lines += [f'net install {metadata["name"]}, from("`sscng_root\'/package")',
              'cd "`sscng_root\'/package"',
              'global S_ADO "BASE;PLUS;PERSONAL;SITE"',
              f'do "{test_file}"',
              'display "SSCNG_REPRODUCTION_COMPLETE"', "log close", ""]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr("package/" + path, content)
        if "stata.toc" not in files:
            archive.writestr("package/stata.toc", f"v 3\nd SSC-NG local reproduction source\np {metadata['name']} Local package\n")
        archive.writestr("SSCNG-CHECKS.txt", "\n".join(text))
        archive.writestr("reproduce.do", "\n".join(lines))
    return output.getvalue()
