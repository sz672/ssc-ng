# Changelog

## 0.2.0 - 2026-09-28

- Local Python service for package submissions, recorded checks, reviewer
  decisions, and an approved release archive.
- Real checks using locally installed Stata and submitted smoke-test assertions.
- Persistent runtime data under `.sscng/`, excluded from source Git history.
- Exact dependencies resolved from approved local package versions.
- Archived release downloads, local Stata installation URLs, and restoration
  checks in separate pilot libraries.
- Daily and manual captures of the local release catalog, without backfilling
  dates or collecting external SSC data.
- Operator-recorded maintainer-transfer evidence and approvals, retaining
  existing release bundles and their original metadata.
- New immutable check records for retries after dependency or runtime failures.
- Inspectable arithmetic package sources for an initial release, an update,
  and the helper required by that update.
- Deterministic example ZIP builder and revised local-pilot walkthrough.

Execution is for trusted packages on a single user's machine; multi-user authentication, independent
maintainer-identity and consent verification, public-code isolation, and external
SSC mirroring remain future work.

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
