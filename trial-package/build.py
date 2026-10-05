#!/usr/bin/env python3
"""Build reproducible, flat upload ZIPs for the trial fixtures and real fre.

Run: python3 trial-package/build.py
Use --check to verify existing ZIPs and manifest without writing changes.
Uses only Python's standard library; Stata is not needed to build.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parent
VERSIONS = ("1.0.0", "1.1.0")
FILENAMES = ("sscng_trial_LICENSE.txt", "metadata.json", "sscng_trial_test.do", "sscng_trial.ado",
             "sscng_trial.pkg", "sscng_trial.sthlp", "stata.toc")
PACKAGES = [("sscng_trial", version, version, FILENAMES, f"sscng_trial-{version}.zip")
            for version in VERSIONS] + [
    ("fre", "1.2.5", "fre-1.2.5",
     ("fre.ado", "fre.hlp", "fre.pkg", "LICENSE", "stata.toc", "metadata.json", "fre_check.do", "SOURCE.json"),
     "fre-1.2.5-demo.zip")]


def package_bytes(name: str, version: str, folder: str, filenames: tuple[str, ...]) -> bytes:
    source = ROOT / folder
    metadata = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
    if (metadata["name"], metadata["version"], metadata["dependencies"]) != (name, version, []):
        raise ValueError(f"Fixture metadata does not match its independent package: {source}")
    if (source / "SOURCE.json").exists():
        provenance = json.loads((source / "SOURCE.json").read_text(encoding="utf-8"))
        for filename, record in provenance["upstream_files"].items():
            if hashlib.sha256((source / filename).read_bytes()).hexdigest() != record["sha256"]:
                raise ValueError(f"Upstream file differs from the pinned source: {source / filename}")
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, "w", compression=zipfile.ZIP_STORED) as archive:
        for filename in filenames:
            info = zipfile.ZipInfo(filename, date_time=(2026, 10, 5, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, (source / filename).read_bytes())
    return memory.getvalue()


def build_all(output: Path | None = None, *, check: bool = False) -> list[dict]:
    output = Path(output) if output is not None else ROOT / "dist"
    if not check:
        output.mkdir(parents=True, exist_ok=True)
    built = []
    for name, version, folder, filenames, filename in PACKAGES:
        data = package_bytes(name, version, folder, filenames)
        path = output / filename
        if check:
            if not path.is_file() or path.read_bytes() != data:
                raise ValueError(f"Missing or stale build: {path}")
        elif not path.exists() or path.read_bytes() != data:
            path.write_bytes(data)
        built.append({"name": name, "version": version,
                      "file": filename, "size": len(data),
                      "sha256": hashlib.sha256(data).hexdigest(),
                      "dependencies": [], "license": "MIT"})
    manifest = (json.dumps({"packages": built}, indent=2) + "\n").encode("utf-8")
    manifest_path = output / "manifest.json"
    if check:
        if not manifest_path.is_file() or manifest_path.read_bytes() != manifest:
            raise ValueError(f"Missing or stale manifest: {manifest_path}")
    elif not manifest_path.exists() or manifest_path.read_bytes() != manifest:
        manifest_path.write_bytes(manifest)
    return built


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for package in build_all(args.output, check=args.check):
        print(f"{args.output / package['file']}  sha256={package['sha256']}")


if __name__ == "__main__":
    main()
