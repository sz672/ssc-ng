# SSC-NG local pilot

Version **0.2.0**. A local Stata package submission, checking, review, and release
pilot. It extends the browser prototype preserved by the Git tag `v0.1.0`.

The pilot receives real package ZIPs, records submissions on disk, runs their
checks with a locally installed Stata, and lets a reviewer approve a checked
candidate for the local release archive. It includes small arithmetic packages
for demonstrating the workflow without using another author's software.

## Run locally

Use Python 3.10 or later and a licensed installation of Stata. The included packages require
Stata 16 or later. From this repository's root, run:

```sh
python3 server.py --port 8765
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). Stop the server with **Ctrl+C**.
Opening `index.html` directly or using `python3 -m http.server` does not start the
pilot backend.

The service looks for a local Stata executable. If it is not found automatically,
provide its path with `--stata` or the `SSCNG_STATA` environment variable. For
example, on this Mac:

```sh
python3 server.py --port 8765 --stata "/Applications/StataNow/StataBE.app/Contents/MacOS/StataBE"
```

The service stores working data under `.sscng/`. This directory is ignored by Git;
submissions and releases persist when the browser reloads or the server restarts.
Keep this directory if you want to retain the local archive.

For a separate meeting run without replacing existing data, select a new directory:

```sh
python3 server.py --port 8765 --data-dir .sscng/meeting-run
```

## Try the supplied packages

Use the built-in examples in the interface, or build their ZIPs yourself:

```sh
python3 examples/build_examples.py
```

The script writes reproducible ZIPs to `.sscng/examples/`:

| Example | Command result | Dependency |
| --- | --- | --- |
| `sscng_example` 1.0.0 | `sscng_example, value(2)` returns `r(result) = 4` | None |
| `sscng_helper` 0.1.0 | `sscng_helper, value(2)` returns `r(result) = 4` | None |
| `sscng_example` 1.1.0 | `sscng_example, value(2)` returns `r(result) = 5` | `sscng_helper` 0.1.0 |

First submit example 1.0.0, wait for checks, and approve it. Next submit example
1.1.0 before publishing its helper: its dependency is missing from the local
archive. Submit and approve helper 0.1.0 after its checks pass. Return to the
failed example 1.1.0 and choose **Create revised submission**. Leave its ZIP
field empty to reuse the saved source, confirm trust, and choose **Submit & run
checks**. This creates a new check record and retains the prior failure; unchanged
source can be retried after a failed or unavailable check. Approve the passing
revision and inspect both releases to see the retained older bundle.
The [meeting walkthrough](docs/MEETING_WALKTHROUGH.md) explains this sequence.

These packages have real `.ado` implementations and assertion-based `smoke.do`
files. Their names, maintainer contact, and use cases are demonstration fixtures.
They are not existing SSC packages.

## Package ZIP format

Place the following files directly at the archive root, without an enclosing
folder:

```text
sscng_example.pkg
sscng_example.ado
sscng_example.sthlp
smoke.do
metadata.json
```

The `.pkg` file uses Stata's `v 3` format. Its `f` entries identify the installed
package files; the smoke test and metadata accompany the submission but are not
listed as installed files. `smoke.do` should exercise the package and use Stata
assertions to fail when results are incorrect.

This pilot accepts `v 3`, `d` description lines, plain `f filename` lines, and an
optional `e` end marker. Do not append a description after a filename on an `f`
line; the validator supports this restricted inventory format.

For a manual ZIP upload, fill in the submission form's metadata separately.
`metadata.json` is optional and is not imported into the form automatically.
The included examples carry this file as a readable reference, while their
built-in submission button supplies the metadata for you. The saved form values
are the authoritative submission record. Downloads preserve the original ZIP
bytes, so an embedded metadata file is not rewritten when form metadata changes.

The example metadata and API submission metadata use this shape:

```json
{
  "name": "sscng_example",
  "version": "1.1.0",
  "title": "SSC-NG arithmetic example with dependency",
  "maintainer": "SSC-NG example maintainer",
  "email": "example@example.invalid",
  "stata": "16.0",
  "license": "Unspecified",
  "dependencies": [{"name": "sscng_helper", "version": "0.1.0"}],
  "notes": "Uses the helper to double the input, then adds one."
}
```

Dependencies refer to exact package versions in this pilot's local release
archive. It does not fetch missing dependencies from SSC or other repositories.
In the browser form, enter one dependency per line as `sscng_helper@0.1.0`, or
leave the field empty when there are none.

## Historical releases and captures

An approved release can be downloaded as a ZIP or installed through its local
Stata `net install` URL, such as
`http://127.0.0.1:8765/packages/sscng_example/1.0.0/`. Manual installation of a
package with dependencies requires installing those exact dependencies first.
The pilot's checks and restore workflow resolve the approved local dependency
closure automatically.

Restoring a release runs its installation and smoke test in a separate pilot
library under `.sscng/environment/jobs/`. The recorded library path shows where
that installation was made. It does not change the user's normal Stata `PLUS`
library or restore an entire research environment.

Catalog captures record the highest approved version of each local package and
its bundle hash. The running service records a daily capture for each UTC day
it observes, and a capture can also be requested manually. Days when the service
was stopped are not reconstructed. These captures describe this local archive,
not historical contents of SSC.

## Maintainer records

The **Registry records** screen lets the local operator record a proposed
maintainer transfer, enter the authorization evidence they have reviewed, and
approve the transfer. The decision updates the recorded maintainer while keeping
earlier release files and metadata intact. This is an operator-maintained record;
the pilot does not verify email ownership, obtain prior-owner consent, or enforce
permissions between different users.

## Scope and limits

This is a single-user, trusted-code pilot on your own machine. Only submit and
run packages whose code you trust: Stata code can access the machine with the
permissions of the account running the service. A separate check working
directory is not an operating-system security sandbox.

The pilot does not provide multi-user authentication, verified maintainer
identities, a public submission service, a cluster queue, or external SSC
mirroring. A local approval records the operator's decision; it does not
establish an author's identity or prove a package's scientific correctness.
Archiving one package version also does not preserve every part of a research
environment, such as the Stata executable, operating system, or external data.

The service is intended for the loopback address shown above. GitHub stores this
project's source code; it does not run this Python service or Stata. GitHub Pages
cannot host the functioning pilot backend.

## Project files

| Path | Purpose |
| --- | --- |
| `server.py` | Command-line entry point for the local service |
| `pilot/service.py` | HTTP API, persistent registry, review decisions, and archive workflow |
| `pilot/packages.py` | ZIP and inventory validation, Stata discovery, and check execution |
| `index.html` | Browser interface |
| `assets/styles.css` | Layout and appearance |
| `assets/app.js` | Browser interaction with the backend |
| `examples/` | Inspectable Stata package sources and ZIP builder |
| `.sscng/` | Local runtime data; excluded from Git |
| `VERSION`, `CHANGELOG.md` | Pilot source version and change notes |
| `docs/MEETING_WALKTHROUGH.md` | Demonstration sequence and decisions to discuss |
| `docs/VERSION_CONTROL.md` | Git history, package history, and release checkpoints |
| `tests/` | Automated checks for the implementation |

## Check the implementation

From the repository root:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
```

The optional integration check starts a temporary local registry and exercises
the supplied packages through the submission, dependency, approval, archive, and
restore workflow using real Stata:

```sh
python3 tests/stata_integration.py
```

It requires a working licensed Stata installation; use `SSCNG_STATA` if automatic
discovery cannot find the executable. An unavailable executable or license is an
infrastructure problem and must not be interpreted as a successful package check.

An optional browser smoke test checks the running interface, its tabs, and
desktop/mobile layout. It works with an empty or populated registry and blocks
write requests, so it does not run Stata or change saved records. In a second
terminal, with the local service already running:

```sh
npm install --no-save --package-lock=false playwright
npx playwright install chromium
SSCNG_BASE_URL=http://127.0.0.1:8765 node tests/smoke.cjs
```

Playwright and Chromium are optional testing dependencies; the pilot itself
needs neither. For manual submission/review tests, start a separate service with
`python3 server.py --port 8766 --data-dir .sscng/browser-tests` and use its address
so test decisions stay separate from meeting records.

## Source version control

The existing `v0.1.0` tag identifies the original browser-only prototype. The
`0.2.0` source version identifies this local pilot; editing `VERSION` alone does
not create a Git tag, push a commit, or publish a GitHub Release.
See [version control](docs/VERSION_CONTROL.md) before saving a new checkpoint.

No license has been assigned to the project. The example metadata says
`Unspecified`; settle the project and package licensing before public reuse.
