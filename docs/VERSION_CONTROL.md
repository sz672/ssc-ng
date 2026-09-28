# Version control for SSC-NG

The existing source repository is `ssc-ng`. Continue using its current Git
history and remote; do not initialize another repository over this folder.

## Different histories serve different purposes

| Record | Meaning |
| --- | --- |
| Source Git commit | A saved change to the portal, server, examples, or documentation |
| Source tag `v0.1.0` | The original browser-only meeting prototype |
| Source `VERSION` value `0.2.0` | The current local pilot's version label; not evidence of a published Git tag or Release |
| Submission and check records | Local package candidates and their actual checking/review activity |
| Package release such as `sscng_example` `1.1.0` | An approved Stata package bundle in the local archive |

The `.sscng/` directory holds runtime data and is excluded from Git. Publishing
a package in this pilot does not commit or push the project's source code.
Conversely, pushing the repository does not upload the local package archive
or start a running package service.

Treat published tags and approved package versions as fixed checkpoints. Correct
a released problem in a new source commit or package version so the prior
record remains explainable.

## Save changes with GitHub Desktop

1. Open the existing `ssc-ng` repository in GitHub Desktop.
2. Inspect the **Changes** list and each diff. `.sscng/`, generated ZIPs, logs,
   and local check output should not appear as files to commit.
3. Run the relevant implementation checks and the local demonstration.
4. Commit the intended source changes with a short summary, for example
   `Add local Stata submission and review pilot`.
5. Use **Push origin** to send that commit to the existing remote.

For a change that needs review, create a branch first, publish the branch after
committing, and open a pull request. Confirm your name and intended commit email
in Desktop's Git settings before making commits.

## Preserve and inspect the first meeting version

These commands inspect the existing checkpoint without changing local files:

```sh
git show v0.1.0
git diff v0.1.0 -- README.md
git log --oneline --decorate
```

To open a separate copy of the original prototype while keeping the current
pilot working tree intact, create a separate worktree:

```sh
git worktree add --detach ../ssc-ng-v0.1.0 v0.1.0
```

Open that copy's `index.html` to see the browser-only prototype. Its interactions
are simulated and reset on reload. The current local pilot must be started with
`server.py` and persists data on disk.

## Create a later source release when ready

The working tree's `VERSION` and changelog describe `0.2.0`. They do not publish
it. After reviewing, testing, committing, and pushing the intended source:

1. Confirm that the selected commit is the one you want to preserve.
2. Create a fresh tag such as `v0.2.0` on that commit. Do not move `v0.1.0`.
3. Push the new tag to the existing remote.
4. If desired, create a GitHub Release from that tag and copy its changelog notes.

For example, after verifying the current commit:

```sh
git status
git log -1 --oneline
git tag -a v0.2.0 -m "SSC-NG local pilot v0.2.0"
git push origin v0.2.0
```

Use these commands only when that version is ready. A tag push does not push
uncommitted edits, publish a GitHub Release page, or deploy the backend.

## Local archive backup and hosting

Keep a separate backup of `.sscng/` if its submissions and approved packages
matter. Stop the local service before copying the data directory so files are
not changing during the copy. Source Git history is not a backup of that data.

GitHub Pages can serve static files, but it cannot run this Python service or
licensed Stata checks. The functioning `0.2.0` pilot currently runs on the local
machine. Public hosting would require a separate deployment design, execution
isolation, authentication, and an appropriate Stata licensing arrangement.

No project license has been selected. Package metadata is separate from the
license of the portal's source code.
