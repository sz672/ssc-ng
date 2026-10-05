# SSC-NG 0.1.0 — submission demo

Upload a Stata package, confirm its maintainer, run checks, review changes, and
deliver the approved files to **today's archive**. The existing
[ssc-ng/archive](https://github.com/ssc-ng/archive/) process handles historical
versions. This demo writes locally; it does not publish to the production archive.

**Working rule:** work locally. Do not commit, tag, push, or publish to GitHub
unless explicitly requested. Keep the application label in `VERSION` and add
concise dated changes below. Package versions are separate from this label.

## Run the demo

Use Python 3.10 or later and a licensed Stata installation:

```sh
cd /Users/J.J./Dropbox/ssc-ng
python3 server.py --port 8765
```

Open [http://127.0.0.1:8765/](http://127.0.0.1:8765/) and leave Terminal running.
Restart the service after changing Python code. Opening `index.html` directly
does not start the service. To select Stata explicitly:

```sh
python3 server.py --port 8765 --stata "/Applications/StataNow/StataBE.app/Contents/MacOS/StataBE"
```

If the page reports **Service restart required**, the running Python process is
older than the page. Stop that server with Ctrl+C and restart its original command,
keeping the same port and data directory. The open page reconnects automatically
and retains its selected ZIP and form entries. Different ports can run separate
demo sessions; use the address printed by the server you started.

Only run trusted packages: the local Stata runner is not an operating-system
sandbox. The `sscng_trial` fixtures declare Stata 16; verification used Stata 19.5.

## Try a real Stata package

[Download fre 1.2.5 for the demo](trial-package/dist/fre-1.2.5-demo.zip).
Ben Jann's [fre](https://github.com/benjann/fre) displays frequency tables with
counts, percentages, and missing values. It has no external package dependencies.
The ZIP keeps the original command, `.hlp` help, inventory, table of contents, and
MIT license from the pinned upstream revision. It adds local-trial metadata,
source/checksum records, and `fre_check.do`, which tests a small generated dataset.
Use **Read package details**, then follow the confirmation/review/delivery steps
below. The illustrative submitter contact is for the local trial, not the author.

Once delivered, try it in Stata (use your demo's port):

```stata
net install fre, from("http://127.0.0.1:8766/archive/f/") replace
sysuse auto, clear
fre foreign
```

The table shows domestic and foreign car counts. The supplied check script uses
generated data, so it does not need to download a dataset. Validation used Stata
19.5; upstream declares Stata 9.2 or newer.

## Try a submission and an update

Upload these ZIPs directly, without extracting or wrapping them in another ZIP:

- [sscng_trial-1.0.0.zip](trial-package/dist/sscng_trial-1.0.0.zip) — initial trial.
- [sscng_trial-1.1.0.zip](trial-package/dist/sscng_trial-1.1.0.zip) — optional update.

Both have no dependencies. Metadata fills the form, including the custom
`sscng_trial_test.do` test. Their installed filenames are unique to this package.

1. In **Submit**, select the `1.0.0` ZIP and click **Read package details**.
   Review the imported fields. Import does not submit or execute code.
2. Check the trust box and click **Submit & run checks**. Inspect the results
   and Stata log in **Checks & review**. Failures include suggested fixes;
   **Download check reproduction kit** provides a local recipe.
3. Open **Demo mailbox — no email is sent**, click **Read demo mailbox**, and
   enter the code under **Confirmation code**. Click **Confirm maintainer**.
   Keep the illustrative contact `trial@example.invalid` for this walkthrough.
4. Click **Show changes**, enter a reviewer name and note, then **Approve
   submission**. Approval leaves the archive unchanged, with delivery pending.
5. In **Deliver & archive**, inspect **Preview delivery**, then click **Deliver
   to today's archive**. Successful delivery produces a receipt and a current ZIP.
6. Choose **Submit update** on the current package, import the `1.1.0` ZIP, and
   repeat checks, confirmation, review, and delivery. The comparison shows the
   code, help, tests, and release-note changes.

For requested changes, choose **Create revised submission**. Earlier feedback
remains visible; each revision needs fresh checks, confirmation, and approval.
Use **Retry delivery** after resolving a temporary destination failure. A stale
candidate based on a superseded current package needs a newly reviewed revision.

After delivery, try the command in Stata while the service runs:

```stata
net install sscng_trial, from("http://127.0.0.1:8765/archive/s/") replace
capture program drop sscng_trial
sscng_trial, value(2)
```

Version 1.0.0 returns `4`; version 1.1.0 returns `5`. Use the port of your running
demo, such as `8766`, in the installation URL. These fixtures test the submission
workflow and arithmetic assertions, not statistical correctness.

A delivered name/version cannot be submitted again. To repeat from an empty
registry, choose an unused port and a new data directory:

```sh
python3 server.py --port 8767 --data-dir .sscng/trial-session-2
```

## Package and data layout

The ZIP needs `<name>.pkg`, every file in its inventory, an `.ado` command, a
`.sthlp` or `.hlp` help file, and an executable `.do` test. `metadata.json` is optional but
enables form import. `test_file` selects the test; its default is `smoke.do`.
The pilot accepts `X.Y.Z` versions and exact dependencies already delivered to
its current archive. These are demo policies, not official SSC requirements.
ZIP limits are 10 MiB compressed, 30 MiB expanded, and 200 regular files.

Delivery writes the `.pkg` and inventory-listed files into the package's first
letter directory, such as `.sscng/current-archive/s/sscng_trial.ado`. Give
installed files package-specific names to avoid conflicts. Submission-only
`metadata.json` can stay outside the `.pkg` inventory. The service manages
`stata.toc`; do not list it as an installed file.

| Location | Purpose |
| --- | --- |
| `server.py`, `pilot/` | Submission service, checks, review, and delivery |
| `index.html`, `assets/` | Three-step interface |
| `trial-package/` | Upload ZIPs, source files, builder, and checksums |
| `examples/` | Built-in examples and dependency fixtures used by the demo/tests |
| `tests/` | Regression tests, browser smoke test, and real-Stata integration |
| `docs/ARCHITECTURE.md` | Handoff contract, recovery, and production boundaries |
| `.sscng/` | Saved local data; excluded from Git |

The data root contains `registry.sqlite3`, original ZIPs under `submissions/`,
check workspaces under `jobs/`, current output under `current-archive/`, and
temporary delivery recovery records under `delivery-journals/`. `--data-dir`
selects another root. Existing submissions and legacy records are preserved;
old approvals are not automatically delivered. Historical archive management
and maintainer transfers are outside this service.

## Validation and production boundary

```sh
python3 trial-package/build.py --check
python3 -m unittest discover -s tests -p 'test_*.py'
python3 tests/stata_integration.py
```

Rebuild the trial ZIPs after editing their sources with
`python3 trial-package/build.py`. The final integration command executes only
trusted repository fixtures in temporary registries and requires licensed Stata.

The mailbox is simulated and reviewer names are locally entered. A production
system needs real identity verification, isolated workers for untrusted code,
and an agreed live archive destination. The local handoff bundle, delivery
receipts, retries, and interrupted-write recovery are implemented. Details and
the CRAN, Bioconductor, and Homebrew references are in
[the architecture notes](docs/ARCHITECTURE.md).

## Dated changes

- **2026-10-05 — submission demo checkpoint.** Preserved the previous published
  source at commit `43ba913` with the tag `pre-submission-demo-2026-10-05`.
  To revisit it from a clean working tree, run
  `git switch -c revisit-previous-demo pre-submission-demo-2026-10-05`.
  The checkpoint covers project files; local `.sscng/` data remains outside Git.
- **2026-10-05 — real-package trial.** Added Ben Jann's `fre` 1.2.5 with unchanged
  upstream files and license, a pinned source manifest, local-trial metadata, and
  numeric/string/missing-value/filter checks. The validator now accepts Stata's
  older `.hlp` format as well as `.sthlp`. Validation: all 80 regression tests and
  reproducible ZIP checks passed. Real Stata 19.5 passed the package assertions,
  confirmation/approval/delivery, and installation from the current HTTP archive
  in a temporary registry. The live demos retain no `fre` submission, ready for
  the user's trial.
- **2026-10-05 — fix package-detail import on stale servers.** Added an API
  compatibility check before enabling requests and a clear restart message.
  Refreshed the old server on 8765 and the preview on 8766 with their existing
  data. Verified ZIP import and automatic reconnection in the browser; all 79
  regression tests passed and saved records were unchanged.
- **2026-10-05 — cleanup.** Removed obsolete archive/transfer operations and
  versioned installation routes, redundant smoke packages, a static-site marker,
  generated logs/caches, and duplicated documentation. Kept saved data and trial
  packages. Validation: all 79 regression tests, real Stata 19.5 integration,
  trial ZIP build checks, and whitespace checks passed. The refreshed preview
  retained the same submissions, current packages, and recorded maintainers.
- **2026-10-05 — trial packages.** Added `sscng_trial` 1.0.0 and 1.1.0. Both
  passed metadata import, real Stata 19.5 checks, confirmation, approval, and
  delivery alongside an existing package in a temporary registry.
- **2026-10-04 — submission workflow.** Added metadata import, demo confirmation,
  actionable checks, review comparisons, separate delivery, receipts, retries,
  and recovery. The 79-test suite and Stata 19.5 integration passed at that point.
- **2026-09-28–29 — baseline.** Set the application label to 0.1.0 and supplied
  reproducible example packages. Earlier Git tags remain unchanged.

Application version remains 0.1.0. The trial package's MIT license does not assign
a license to the application as a whole.
