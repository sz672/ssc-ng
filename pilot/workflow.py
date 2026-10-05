"""Local submission identity demonstration and review comparisons.

The demo mailbox is deliberately visible to the single local operator. It
exercises confirmation rules, not real email possession or authenticated roles.
"""
from datetime import datetime, timedelta, timezone
import difflib
import hashlib
import hmac
import secrets
import uuid

from .packages import bundle_files


class WorkflowMixin:
    def owner_for(self, name):
        owner = self.setting(f"owner:{name}")
        if owner and owner.get("email"):
            return {"name": name, **owner}
        candidates = [item for item in self.rows("submissions")
                      if item["name"] == name and item["status"] == "approved"]
        releases = [item for item in self.rows("releases") if item["name"] == name]
        records = candidates or releases
        if records and records[0]["metadata"].get("email"):
            metadata = records[0]["metadata"]
            return {"name": name, "maintainer": metadata["maintainer"], "email": metadata["email"]}
        return None

    def owners(self):
        names = {item["name"] for item in self.rows("submissions")} | {item["name"] for item in self.rows("releases")}
        return [owner for name in sorted(names) if (owner := self.owner_for(name))]

    def public_submission(self, item):
        confirmation = item.get("confirmation")
        if confirmation and confirmation["status"] == "pending" and confirmation["expires_at"] < datetime.now(timezone.utc).isoformat():
            item = {**item, "confirmation": {**confirmation, "status": "expired"}}
        return item

    def request_confirmation(self, identifier):
        from .service import APIError, now
        with self.lock:
            submission = self.get("submissions", identifier)
            if submission["status"] in ("approved", "superseded"):
                raise APIError("This submission no longer needs confirmation.", 409)
            owner = self.owner_for(submission["name"])
            email = owner["email"] if owner else submission["metadata"]["email"]
            if email.casefold() != submission["metadata"]["email"].casefold():
                raise APIError("This package belongs to another maintainer. Use its recorded contact or ask the operator to resolve ownership.", 409)
            code = secrets.token_hex(4).upper()
            expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds")
            submission["confirmation"] = {"status": "pending", "email": email, "expires_at": expires,
                                          "mode": "demo_mailbox", "attempts_remaining": 5}
            challenge = {"digest": hashlib.sha256(code.encode()).hexdigest(), "fingerprint": submission["fingerprint"],
                         "email": email, "expires_at": expires, "attempts": 0, "used": False}
            message = {"id": uuid.uuid4().hex, "submission_id": identifier, "to": email,
                       "subject": f"Confirm {submission['name']} {submission['version']}", "code": code,
                       "created_at": now(), "expires_at": expires, "mode": "demo_mailbox"}
            with self.connect() as db:
                from .service import encoded
                db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (f"confirmation:{identifier}", encoded(challenge)))
                db.execute("UPDATE submissions SET value=? WHERE id=?", (encoded(submission), identifier))
                db.execute("DELETE FROM mailbox WHERE submission_id=?", (identifier,))
                db.execute("INSERT INTO mailbox(id,submission_id,value) VALUES(?,?,?)", (message["id"], identifier, encoded(message)))
            return self.public_submission(submission)

    def demo_mailbox(self):
        return {"mode": "demo_mailbox", "notice": "Local simulation only. No email is sent; codes are visible to the local operator.",
                "messages": self.rows("mailbox")}

    def confirm(self, identifier, body):
        from .service import APIError, now
        with self.lock:
            submission = self.get("submissions", identifier)
            if submission["status"] in ("approved", "superseded"):
                raise APIError("Confirmation is closed for this submission.", 409)
            challenge = self.setting(f"confirmation:{identifier}")
            if not challenge:
                raise APIError("Request a confirmation code for this submission first.", 409)
            if challenge["used"]:
                raise APIError("This confirmation code has already been used.", 409)
            if challenge["expires_at"] < now():
                raise APIError("Confirmation expired. Request a new code.", 409)
            if challenge["attempts"] >= 5:
                raise APIError("Confirmation is locked after five failed attempts. Request a new code.", 409)
            owner = self.owner_for(submission["name"])
            email = owner["email"] if owner else submission["metadata"]["email"]
            if challenge["fingerprint"] != submission["fingerprint"] or email.casefold() != challenge["email"].casefold():
                raise APIError("The candidate or recorded maintainer changed. Request a new confirmation.", 409)
            code = body.get("code", "")
            if not isinstance(code, str) or len(code) > 100:
                raise APIError("Enter the confirmation code from the demo mailbox.")
            digest = hashlib.sha256(code.strip().upper().encode()).hexdigest()
            if not hmac.compare_digest(digest, challenge["digest"]):
                challenge["attempts"] += 1
                submission["confirmation"]["attempts_remaining"] = 5 - challenge["attempts"]
                if challenge["attempts"] >= 5:
                    submission["confirmation"]["status"] = "locked"
                self.set_setting(f"confirmation:{identifier}", challenge)
                self.save("submissions", submission)
                raise APIError("Incorrect confirmation code. Use the latest code for this submission.")
            challenge["used"] = True
            submission["confirmation"].update(status="verified", confirmed_at=now(), fingerprint=submission["fingerprint"])
            self.set_setting(f"confirmation:{identifier}", challenge)
            self.save("submissions", submission)
            self.event("confirmation", f"Demo maintainer confirmation recorded for {submission['name']}@{submission['version']}.")
            return submission

    def comparison(self, identifier):
        with self.lock:
            item = self.get("submissions", identifier)
            current = (self.setting("current_archive") or {}).get(item["name"])
            files = bundle_files(self.zip_for(item))
            base_files = {}
            base_metadata = {}
            if current:
                base = self.get("submissions", current["submission_id"])
                base_files = bundle_files(self.zip_for(base))
                base_metadata = current["metadata"]
            changed = []
            for path in sorted(files.keys() & base_files.keys()):
                before, after = base_files[path], files[path]
                if before != after:
                    if len(before) + len(after) > 200_000 or b"\0" in before + after:
                        diff = "Binary or large file changed. Download the candidate and current package to inspect."
                    else:
                        diff = "".join(difflib.unified_diff(before.decode("utf-8", "replace").splitlines(True), after.decode("utf-8", "replace").splitlines(True), fromfile=f"current/{path}", tofile=f"candidate/{path}"))[:50_000]
                    changed.append({"path": path, "diff": diff})
            parent = self.get("submissions", item["parent_id"]) if item.get("parent_id") else None
            delivered = item.get("delivery", {}).get("status") == "delivered" or bool(item.get("archive_files"))
            return {"base": {key: current[key] for key in ("name", "version", "submission_id")} if current else None,
                    "stale": not delivered and item.get("base_submission_id") != (current["submission_id"] if current else None),
                    "files": {"added": sorted(files.keys() - base_files.keys()), "removed": sorted(base_files.keys() - files.keys()), "changed": changed},
                    "metadata": [{"field": key, "before": base_metadata.get(key), "after": item["metadata"].get(key)}
                                 for key in sorted(item["metadata"].keys() | base_metadata.keys()) if base_metadata.get(key) != item["metadata"].get(key)],
                    "parent": {key: parent.get(key) for key in ("id", "review_note", "reviewer")} if parent else None}
