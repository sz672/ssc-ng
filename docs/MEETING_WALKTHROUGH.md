# First-meeting walkthrough

Allow about ten minutes. Open index.html in a browser. Everything displayed is
fictional, and each change of demo case starts over. Reloading also resets state.

## 1. Metadata and an ordinary update

Choose **Routine update**. Inspect the required fields and switch the sample input
between ZIP and Git. Click **Run demo checks**, inspect the results, then click
**Approve demo release** and **View package history**.

Discuss: Which fields must be required immediately? Which checks should initially
produce warnings? Does a routine update still need a brief human approval?

## 2. Installation is different from working execution

Choose **Missing dependency**, then **Run demo checks**. Installation passes while
the example fails. Approval is blocked. Add a reviewer note and select **Request
changes**, then **Add missing dependency & rerun**. The revision number increases.
Inspect the new results, approve, and see the dependency recorded in release history.

Discuss: Must every submission include a runnable example? What explains a failure
well enough for an author to correct it? How should unavailable test infrastructure
be distinguished from broken package code?

## 3. Ownership changes

Choose **Maintainer transfer**, then **Run demo checks**. Approval remains blocked
until **Simulate prior-owner confirmation** is selected. Approve the transfer and
open package history. The maintainer changes; the existing release does not.

Discuss: What evidence should authorize a transfer? What is the recovery procedure
when the previous maintainer cannot be reached?

## 4. Historical releases

Open **Package history** and select 1.0.1 or 1.0.0. Compare the file versions and
diffs. The helper file retains version 0.4.0. Expand the daily-capture example:
unchanged days point to the same bundle. **Restore ... in demo** changes only the
simulated environment label. Installation commands use a non-working example URL.

Discuss: Is this the right distinction between package releases, file versions,
submission revisions, and observations from a nightly archive?

## Decisions to record

- Required metadata for the pilot.
- Checks that block publication and checks that warn.
- Reviewer responsibility and escalation path.
- Evidence needed for maintainer claims and transfers.
- A small set of real packages and authors for a later supervised pilot.

## Pilot measures to collect later

| Measure | Suggested definition |
| --- | --- |
| Reviewer time | Active minutes spent reviewing each submission, excluding queue time |
| Correction rounds | Number of revised submissions before acceptance |
| Publication time | Time from initial submission to publication, also separating author and queue delays |
| Restoration success | Archived releases that install and pass their recorded example, divided by eligible releases attempted |

The demo does not measure these outcomes. Collect baseline results from the current
process before making claims about improvement.
