# Local pilot meeting walkthrough

Allow about ten minutes. Start `python3 server.py --port 8765` from the project
root and open <http://127.0.0.1:8765>. Use the built-in example packages or build
their ZIPs with `python3 examples/build_examples.py`.

For a fresh archive without deleting previous work, use a new data directory:

```sh
python3 server.py --port 8765 --data-dir .sscng/meeting-run
```

Choose another directory name for each fresh demonstration. If automatic Stata
discovery fails, pass `--stata /path/to/your/StataExecutable`.

The examples contain real, small Stata programs and assertions. The workflow
persists submissions, check results, decisions, and locally approved releases.
Refreshing the page does not reset it. Package contacts are illustrative and
the pilot has no authenticated reviewer accounts.

## 1. A package without dependencies

Select `sscng_example` version `1.0.0`. Inspect its metadata and source, confirm
that this supplied code is trusted, and choose **Submit example for checks**.
Checks start automatically; inspect their recorded Stata output. The smoke test calls `sscng_example, value(2)` and
asserts that `r(result)` equals `4`; it also checks a negative input.

Approve the passing candidate and open its package history. The local archive
now contains a real release bundle. Download it to inspect the `.pkg`, `.ado`,
help file, metadata, and smoke test.

Discuss: Which metadata is mandatory? What should a reviewer examine after the
automated checks pass? What must be recorded with an approval?

## 2. An update with a missing dependency

Submit `sscng_example` version `1.1.0` before approving the helper package. This
update calls `sscng_helper` version `0.1.0` and then adds one to its result. Its
metadata declares that exact dependency. Run checks and inspect the missing
dependency result. The candidate cannot be approved while its checks fail.

This deliberately demonstrates a dependency absent from the local archive. It
is not evidence that an external package or the public SSC archive is broken.
If the helper is already present from an earlier demonstration, this step will
no longer reproduce the missing-dependency case.

Discuss: Must dependencies be approved before their dependants? How should the
service distinguish missing dependencies, a failed test, and unavailable Stata?

## 3. Make the dependency available and check again

Submit `sscng_helper` version `0.1.0`, run its real Stata assertions, and approve
the passing helper. In **Checks & review**, select the failed example `1.1.0`
and choose **Create revised submission**. Keep its metadata, leave the ZIP field
empty to reuse the saved bundle, confirm trust again, and choose **Submit & run
checks**. The new revision receives its own check record; the failed record is
retained. Unchanged source may be retried after a failed or unavailable check.

The new test now verifies that `value(2)` returns `5`, and that `value(-3)` returns
`-5`. Approve the passing revision. If uploading a ZIP manually instead, complete
the metadata fields yourself: the form does not import `metadata.json`.

The changed arithmetic is intentional: it makes the two example releases
visibly different. It is not a proposed change to any research command.

Discuss: Should reviewers see the prior failed check and the dependency version
used by the successful check? What should happen if a dependency is updated
after a candidate has been checked?

## 4. Inspect historical releases

Open the history for `sscng_example` and inspect versions `1.0.0` and `1.1.0`.
Compare their source files, metadata, and dependencies. Both release bundles
remain available; approving `1.1.0` does not replace `1.0.0`.

Restore version `1.0.0` and inspect the real installation and smoke-test result.
The pilot creates a separate library for this run and records its path. The
assertions should again confirm that `value(2)` returns `4`. Restoring `1.1.0`
also installs its approved helper dependency and checks for `5`. These operations
do not replace packages in your normal Stata `PLUS` directory.

Inspect the local catalog captures. A capture records the highest approved
version of each package and its bundle hash. The service captures each UTC day
while running and supports manual captures; it does not invent missing days or
collect the public SSC archive.

The source repository has its own history: `v0.1.0` preserves the earlier
browser prototype. Package release `1.0.0` is a different version number and
does not create a Git commit or tag for this website.

Discuss: What else must be preserved to rerun an old analysis: Stata version,
dependency bundles, data, operating system, and execution instructions?

## 5. Record an operator-approved maintainer transfer

Open **Registry records**, select `sscng_example`, and enter a demonstration
successor such as `Example successor` with `successor@example.invalid`. Describe
the example authorization evidence, save the request, and inspect it before
choosing **Approve recorded transfer**.

The current recorded maintainer changes, while archived package contents and
their original metadata remain intact. This records the local operator's
decision. It does not send a verification email, independently check consent,
or create authenticated access for the successor.

Discuss: What evidence should authorize a real transfer? Who may review it?
How should recovery work when the prior maintainer cannot be reached?

## Decisions to record

- Required package metadata and smoke-test expectations.
- Which failures block approval and how reviewers record exceptions.
- Dependency acceptance and exact-version retention rules.
- Evidence, authentication, and permissions required for real maintainer transfers.
- A small, consenting group of package authors for a later supervised pilot.
- Execution isolation and operational requirements before accepting public code.

## Evidence to collect

| Measure | Suggested definition |
| --- | --- |
| Reviewer time | Active minutes spent reviewing each submission, excluding queue time |
| Correction rounds | Revised submissions before acceptance |
| Publication time | Submission to approval, separating author and check delays |
| Historical installation success | Archived bundles that install and pass their recorded examples, divided by attempts |

The local pilot can supply individual records and logs. These are not yet a
comparative evaluation of SSC-NG. Collect baseline observations from the current
process before claiming an improvement.
