"""Write a current SSC-style working tree; historical archiving is external."""
from contextlib import contextmanager
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .packages import _manifest_files, _safe_path, bundle_files


def package_files(zip_bytes, name):
    files = bundle_files(zip_bytes)
    inventory = name + ".pkg"
    listed = _manifest_files(files[inventory])
    if any(path.split("/", 1)[0].casefold() == "stata.toc" for path in listed):
        raise ValueError("stata.toc is maintained by the submission service; do not list it in the package inventory.")
    return {f"{name[0]}/{path}": files[path] for path in [inventory, *listed]}


def toc(packages, letter):
    lines = ["v 3", "d SSC-NG current local archive"]
    for name, item in sorted(packages.items()):
        if name[0] == letter:
            title = " ".join(item["metadata"]["title"].split())
            lines.append(f"p {name} {title}")
    return ("\n".join(lines) + "\n").encode()


def safe_destination(root, relative):
    _safe_path(relative)
    target = root / relative
    if root.is_symlink() or any(path.is_symlink() for path in [target, *target.parents] if path == root or root in path.parents):
        raise ValueError("The current archive must not contain symbolic links.")
    return target


def replace_file(path, data):
    """Replace one file atomically, including during a failed write rollback."""
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    path.parent.mkdir(parents=True, exist_ok=True)
    for parent in reversed(missing):
        sync_directory(parent.parent)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".sscng-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def sync_directory(path):
    """Make a rename or deletion durable before committing the database."""
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ArchiveJournal:
    """Durable undo data for one local delivery, outside the public archive."""

    def __init__(self, path, details=None):
        self.path = Path(path)
        self.details = details or {}

    def prepare(self, root, before, after):
        self.details = {**self.details, "root": str(root.resolve()),
                        "before": {path: base64.b64encode(data).decode() if data is not None else None
                                   for path, data in before.items()},
                        "after": {path: hashlib.sha256(data).hexdigest() for path, data in after.items()}}
        replace_file(self.path, json.dumps(self.details, sort_keys=True).encode())

    def load(self):
        self.details = json.loads(self.path.read_text())
        return self.details

    def rollback(self, root):
        if str(root.resolve()) != self.details["root"]:
            raise ValueError("Delivery journal destination does not match the current archive.")
        before = {relative: base64.b64decode(data, validate=True) if data is not None else None
                  for relative, data in self.details["before"].items()}
        # A stopped service may have written any prefix of the change. Refuse to
        # overwrite bytes from an unrelated external change made during downtime.
        for relative, data in before.items():
            path = safe_destination(root, relative)
            current = path.read_bytes() if path.exists() else None
            digest = hashlib.sha256(current).hexdigest() if current is not None else None
            original = hashlib.sha256(data).hexdigest() if data is not None else None
            if digest not in (original, self.details["after"].get(relative)):
                raise ValueError(f"Cannot recover delivery: {relative} changed outside the service.")
        for relative, data in before.items():
            path = safe_destination(root, relative)
            if data is None:
                if path.exists():
                    path.unlink()
                    sync_directory(path.parent)
            else:
                replace_file(path, data)

    def clear(self):
        self.path.unlink(missing_ok=True)
        sync_directory(self.path.parent)


def preflight_current(root, files, previous, packages, candidate):
    """Verify ownership, file types, and current hashes without modifying files."""
    old_files = set(previous.get("files", [])) if previous else set()
    owned = {path.casefold(): item["name"] for item in packages.values()
             if item["name"] != candidate["name"] for path in item["files"]}
    for relative in files:
        key = relative.casefold()
        for other, owner in owned.items():
            if key == other or key.startswith(other + "/") or other.startswith(key + "/"):
                raise ValueError(f"Archive file collision: {relative} belongs to {owner}.")
    letter = candidate["name"][0]
    index = f"{letter}/stata.toc"
    contents = {**files, index: toc({**packages, candidate["name"]: candidate}, letter)}
    targets = set(contents) | old_files
    before = {}
    for relative in sorted(targets):
        path = safe_destination(root, relative)
        # Catch file/directory conflicts before making any changes.
        if path.exists() and not path.is_file():
            raise ValueError(f"Archive path is not a regular file: {relative}.")
        for parent in path.parents:
            if parent == root.parent:
                break
            if parent.exists() and not parent.is_dir():
                raise ValueError(f"Archive directory is blocked by a file: {relative}.")
        data = path.read_bytes() if path.exists() else None
        if relative == index:
            if data is not None and data != toc(packages, letter):
                raise ValueError(f"Current archive index changed outside the service: {relative}.")
        elif relative in old_files:
            if data is None or hashlib.sha256(data).hexdigest() != previous["file_hashes"][relative]:
                raise ValueError(f"Current archive file changed outside the service: {relative}.")
        elif data is not None:
            raise ValueError(f"Archive file collision: {relative} already exists outside this package.")
        before[relative] = data
    return contents, before, old_files


@contextmanager
def write_current(root, files, previous, packages, candidate, journal=None):
    """Preflight every path, write checked files, roll back on ordinary failures.

    The caller holds its publication lock and commits the delivery/manifest inside
    this context. This local adapter does not commit Git or create dated copies.
    """
    contents, before, old_files = preflight_current(root, files, previous, packages, candidate)
    if journal is not None:
        journal.prepare(root, before, contents)
    touched = []
    try:
        for relative, data in sorted(contents.items()):
            touched.append(relative)
            replace_file(safe_destination(root, relative), data)
        for relative in sorted(old_files - files.keys()):
            touched.append(relative)
            path = safe_destination(root, relative)
            path.unlink()
            sync_directory(path.parent)
        yield
    except BaseException:
        for relative in reversed(touched):
            path = safe_destination(root, relative)
            if before[relative] is None:
                if path.exists():
                    path.unlink()
                    sync_directory(path.parent)
            else:
                replace_file(path, before[relative])
        if journal is not None:
            journal.clear()
        raise
    else:
        if journal is not None:
            # The DB is committed. A leftover journal is safe: restart checks the
            # committed receipt before deciding whether to undo the file writes.
            try:
                journal.clear()
            except OSError:
                pass
