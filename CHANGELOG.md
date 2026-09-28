# Changelog

## 0.1.0 - 2026-09-27

Initial SSC-NG meeting prototype.

- Editable package metadata with ZIP and Git submission examples.
- Simulated installation, execution, and dependency checks.
- Reviewer decisions kept separate from automated check results.
- Approval tied to a checked candidate; approved fields become read-only.
- Three cases: routine update, missing dependency, and maintainer transfer.
- Historical releases, file differences, unchanged helper-file versions, and daily capture examples.
- Simulated restoration of an earlier package release.
- Standalone page with no build step or runtime dependencies.
- GitHub setup, release-tagging, and meeting walkthrough documentation.

This is a UI prototype. It does not run Stata, receive uploads, create Git commits,
verify identities, or publish real packages. In-memory demo state resets on reload
or when the selected case changes.
