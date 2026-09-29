# SSC-NG 0.1.0 — instructions and change record

This is the first local baseline for the revised SSC-NG direction: receive a
Stata package, check it, review it, preserve versions, and install an older version.
It is a development pilot, separate from the public [SSC-NG website](https://ssc-ng.net/).
The proposed architecture follows the [published development plan](https://ssc-ng.net/development-plan/).

**Working rule:** work locally. Do not commit, tag, push, or publish to GitHub
unless you explicitly ask. Update this file when a version changes.

## 1. What to keep and what each file does

There are two project documents to read:

| File | Use |
| --- | --- |
| **README.md — this file** | Start the demo, upload/install the smoke packages, find saved data, and read the version change record |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Understand storage, original-source links, platform-neutral submissions, PR review, and recovery without GitHub; distinguishes current features from proposals |

The working application and its tests remain in place:

| File or directory | Use |
| --- | --- |
| `VERSION` | Current application label, `0.1.0`; also read by the API and displayed in the running app |
| `server.py` | Starts the local web service |
| `pilot/service.py` | Uploads, database records, checks queue, approvals, release downloads and Stata endpoints |
| `pilot/packages.py` | Validates package ZIPs and runs Stata checks |
| `index.html`, `assets/` | Browser interface, styling, and interactions |
| `smoke-package/1.0.0/`, `smoke-package/1.1.0/` | Inspectable source files for the two new smoke-package versions |
| `smoke-package/build.py` | Rebuilds their ZIPs with stable archive metadata |
| `smoke-package/dist/` | Two ready-to-upload ZIPs and `manifest.json` listing their hashes and metadata |
| `examples/` | Older built-in dependency fixtures used by the app and its regression tests; not needed for the new smoke exercise |
| `tests/` | Automated implementation checks and optional real-Stata/browser checks |
| `.sscng/` | Saved local registry and package files; hidden in Finder and excluded from Git |
| `.git/`, `.gitignore`, `.gitattributes`, `.github/`, `.nojekyll` | Existing source history and repository configuration; `.github` contains the development PR template |

The separate walkthrough, version-control guide, research note, and changelog
were consolidated into these two documents. Source code, test fixtures, and
existing saved submissions were preserved.

## 2. Open the working demo

In Terminal:

```sh
cd /Users/J.J./Dropbox/ssc-ng
python3 server.py --port 8765
```

Open [http://127.0.0.1:8765/](http://127.0.0.1:8765/) and leave Terminal running.
Use Python 3.10 or later and a licensed Stata installation. The smoke packages
require Stata 16 or later. This Mac's Stata can also be selected explicitly:

```sh
python3 server.py --port 8765 --stata "/Applications/StataNow/StataBE.app/Contents/MacOS/StataBE"
```

If a service is already running, stop it with Ctrl+C in its Terminal and restart
it to load code/version changes. Double-clicking `index.html` shows a link and
startup instructions; package operations require the Python service.

The public homepage currently links to the legacy SSC submission process. These
ZIPs target **this local pilot**; preparing them does not submit them to SSC,
deploy the app, or create a hosted pull request.

## 3. Upload the two smoke versions

Use these finished archives; do not ZIP their containing folders:

- [sscng_smoke-1.0.0.zip](smoke-package/dist/sscng_smoke-1.0.0.zip)
- [sscng_smoke-1.1.0.zip](smoke-package/dist/sscng_smoke-1.1.0.zip)

Both are dependency-free. In **Submit a package**, enter:

| Form field | Value |
| --- | --- |
| Package name | `sscng_smoke` |
| Release version | `1.0.0` for the first ZIP; `1.1.0` for the second |
| Title | `SSC-NG standalone arithmetic smoke package` |
| Maintainer | `SSC-NG smoke-package contributors` |
| Email | `smoke@example.invalid` — an illustrative, non-deliverable contact |
| Minimum Stata version | `16.0` |
| License | `MIT` |
| Dependencies | Leave blank |
| Release notes | `1.0.0: doubles the input.` or `1.1.0: doubles the input and adds one.` |
| Package ZIP | The matching ZIP above |

`metadata.json` inside each ZIP provides the exact example metadata. The current
form does **not** import it automatically; the entered form values are the saved
submission metadata. The original ZIP bytes remain unchanged.

1. Upload `1.0.0`, confirm trust in this supplied example, then choose
   **Submit & run checks**.
2. In **Checks & review**, wait for **Passed**, inspect the log, and approve it.
   If Stata is unavailable, fix its executable/license setup before approval.
3. Repeat for `1.1.0`, with the second ZIP and matching version/notes. It requires
   no helper package. Clear any unfinished revision before starting a new upload.
4. In **Package history**, select either version. Try **Download release ZIP**,
   **Compare with previous release**, and **Restore & verify this version**.
   The restore action shows its checks, uses a separate managed Stata library,
   and records a successful managed-library selection.

The first version returns `4` for `value(2)`; the second returns `5`. Each smoke
test also checks zero, negative and fractional input, and the returned version.
These are arithmetic fixtures for testing the workflow, not statistical methods.

An already approved name/version cannot be overwritten. To repeat the exercise
from an empty registry while retaining current records, start another instance
on a different port and with a fresh data directory:

```sh
python3 server.py --port 8766 --data-dir .sscng/smoke-session-2
```

Open `http://127.0.0.1:8766/` and use port `8766` in installation URLs as well.
Choose a new directory for each empty session; do not delete the old archive.

## 4. Pull/download and install a saved version

Here “pull a package” means download/install an archived release. A **pull
request** is a proposed registry change for review. The current local review
screen does not create GitHub or Forgejo PRs.

After both approvals, keep the service running. In Stata, install version 1.0.0:

```stata
net install sscng_smoke, from("http://127.0.0.1:8765/packages/sscng_smoke/1.0.0/") replace
capture program drop sscng_smoke
sscng_smoke, value(2)
assert r(result) == 4
```

Then install version 1.1.0:

```stata
net install sscng_smoke, from("http://127.0.0.1:8765/packages/sscng_smoke/1.1.0/") replace
capture program drop sscng_smoke
sscng_smoke, value(2)
assert r(result) == 5
```

Repeat the first block to return to the older version. Dropping the loaded
program makes Stata load the newly installed file. These manual commands replace
the installed example in Stata's current installation location; the website's
**Restore & verify** action instead uses a separate pilot library. Stata documents
this distribution mechanism in its [net manual](https://www.stata.com/manuals/rnet.pdf).

## 5. Where the packages and versions are stored

Before upload, the two ZIPs are in:

```text
/Users/J.J./Dropbox/ssc-ng/smoke-package/dist/
```

After upload to the default service, the original ZIP is also preserved at:

```text
/Users/J.J./Dropbox/ssc-ng/.sscng/submissions/<submission-id>/source.zip
```

Find the submission ID in **Checks & review**. Metadata, release-to-submission
links, hashes, decisions, and logs are in `.sscng/registry.sqlite3`. Approval
references the saved submission ZIP instead of copying it again. Both package
versions survive restarting the service. A different `--data-dir` changes these
runtime locations.

GitHub holds the application's committed source history. It does not currently
back up `.sscng/`. The two small smoke ZIPs are included with their source files
so they can be downloaded directly from the repository. They can be rebuilt with:

```sh
python3 smoke-package/build.py
```

The architecture document explains the next storage design: one object per unique
ZIP checksum, small version manifests pointing to objects, an independent backup,
and later file-level deduplication if measured storage needs justify it. Those
storage improvements and remote GitHub/Dropbox imports are proposals, not features
implemented by this documentation update.

## 6. Version changes and validation

**Current local baseline: 0.1.0 — 2026-09-28.** The local working version was reset
to `0.1.0` at your request. Earlier Git tags named `v0.1.0` and `v0.2.0` remain
historical checkpoints and were not moved or recreated. This baseline is saved as
a separate checkpoint; it does not replace either historical tag.

| Change | What it provides |
| --- | --- |
| Consolidated documents | One instruction/change record and one architecture reference aligned with the published plan |
| Two new standalone package versions | Direct ZIP upload and visible older/newer installation results, without a helper dependency |
| One application version source | API and server label now read `VERSION` |
| Architecture decisions | Original-source references plus captured files; neutral intake; proposed deduplication; GitHub-independent review/storage and recovery |
| Preserved local entry fix | Relative asset paths and startup guidance when opening `index.html` directly |

**2026-09-29 — GitHub checkpoint packaging (version remains 0.1.0).** At your
request, include both ready-to-upload smoke ZIPs in the repository using two
specific `.gitignore` exceptions, and update this file to describe their location.
The ZIPs and manifest match a deterministic rebuild. No registry data migration
is needed; `.sscng/` remains excluded from Git.

Historical context retained from the removed changelog: the original `v0.1.0`
checkpoint was a browser-only prototype with simulated checks. The historical
`v0.2.0` checkpoint introduced the Python service, real Stata checks, persistent
records, exact dependencies, archived installs/restores, catalog captures, and
operator-recorded maintainer transfers. Those implemented capabilities remain.

Validation on 2026-09-28: all 34 application regression tests passed. Both new
ZIPs passed validation and real Stata 19.5 checks through a temporary HTTP
registry. Approval, byte-identical archive downloads, and actual Stata `net install`
passed for both versions. Restoring `1.0.0` after approving `1.1.0` also passed.
These checks used separate temporary data and left the saved demo records intact.
Repeated builds matched the ZIPs and hashes in `smoke-package/dist/manifest.json`.
The example declares Stata 16 as its minimum; this run tested Stata 19.5 only.

To rerun the application's regression suite or its older dependency integration
exercise:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
python3 tests/stata_integration.py
```

For every future application version, add a dated entry here recording **what
changed, affected files, validation, and any data migration**. Update `VERSION`
when choosing that version. Keep package release numbers separate from the
application version. A future Git commit/tag/push requires your explicit request;
choose an unused tag rather than overwriting a historical checkpoint.

Only execute trusted packages on this Mac: the local runner is not a security
sandbox. The smoke package has its own MIT license; this does not assign a new
license to the SSC-NG application as a whole.
