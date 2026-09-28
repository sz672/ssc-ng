#!/usr/bin/env python3
"""Build the three local pilot fixtures as deterministic, flat Stata ZIPs."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import zipfile


SOURCE_ROOT = Path(__file__).resolve().parent
EXAMPLES = (
    ("sscng-example-1.0.0", "Example 1.0.0 — no dependencies", "sscng_example", "1.0.0"),
    ("sscng-helper-0.1.0", "Helper 0.1.0 — dependency for the update", "sscng_helper", "0.1.0"),
    ("sscng-example-1.1.0", "Example 1.1.0 — requires the helper", "sscng_example", "1.1.0"),
)


def build_all(output: Path) -> list[dict]:
    """Return id, label, ZIP path (Path), and metadata for every built fixture.

    ZIP entry order, timestamps, permissions, and storage method are fixed, so
    identical source files produce identical bytes across builds and platforms.
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    built = []
    for example_id, label, package, version in EXAMPLES:
        source = SOURCE_ROOT / package / version
        metadata = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
        if metadata["name"] != package or metadata["version"] != version:
            raise ValueError(f"Metadata does not match fixture directory: {source}")
        filenames = sorted((f"{package}.pkg", f"{package}.ado", f"{package}.sthlp", "smoke.do", "metadata.json"))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
            for filename in filenames:
                entry = zipfile.ZipInfo(filename, date_time=(2026, 9, 27, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = 0o100644 << 16
                entry.compress_type = zipfile.ZIP_STORED
                archive.writestr(entry, (source / filename).read_bytes())
        path = output / f"{example_id}.zip"
        contents = buffer.getvalue()
        if not path.exists() or path.read_bytes() != contents:
            path.write_bytes(contents)
        built.append({"id": example_id, "label": label, "path": path, "metadata": metadata})
    return built


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=SOURCE_ROOT.parent / ".sscng" / "examples")
    args = parser.parse_args()
    for example in build_all(args.output):
        print(f"{example['id']}: {example['path']}")


if __name__ == "__main__":
    main()
