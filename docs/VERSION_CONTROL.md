# Version control for the SSC-NG demo

This guide creates history under **your own Git identity**. The supplied ZIP has no `.git` directory, remote repository, or prewritten commit history.

## Three different kinds of version

| Term | What it records |
| --- | --- |
| Git commit | A saved change to this website's source files, with an author, message, and parent history |
| Release tag, such as `v0.1.0` | A named checkpoint pointing to one website commit; a GitHub Release can attach notes and downloads to it |
| Package release inside the demo, such as `1.1.0` | Fictional Stata package data in the browser; it does not create a Git commit or tag |

The `VERSION` file contains `0.1.0`. `CHANGELOG.md` describes changes to the website. Treat published release tags as fixed: correct a released problem in a new commit and release rather than moving the old tag.

## Simplest first upload: your browser

1. [Create a new repository on GitHub](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository) under your account. Make it empty: do not initialize it with a README, `.gitignore`, or license.
2. Click **uploading an existing file** on the empty repository page, or **Add file → Upload files** if available.
3. Upload the **extracted project contents**, including `assets`, `docs`, and dotfiles, rather than the ZIP. Check that `index.html` is directly at the repository root. On a Mac, **Command+Shift+.** makes dotfiles visible in Finder.
4. Commit with the message **`Initial SSC-NG demo (v0.1.0)`**.
5. Optionally open **Releases → Draft a new release**, create the tag **`v0.1.0`** targeting the uploaded `main` commit, and add the changelog notes. Publish when ready.

This creates real Git history in GitHub under your account. To edit locally later, **clone this existing repository** using GitHub Desktop or Git; do not initialize a separate repository over it. See [GitHub's file-upload instructions](https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository) and [release instructions](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository).

## Alternative first upload: GitHub Desktop

1. Sign in to GitHub Desktop with your own GitHub account. Check its **Git** settings so new commits use your name and intended email. A GitHub-provided private commit email is also an option.
2. Choose **File → New repository**. Use a name such as `ssc-ng-demo` and select a local parent folder. Leave the README option unchecked and choose no license. Create the repository. Desktop may create an initial commit for its generated files.
3. Extract the ZIP. Copy **everything inside the extracted project folder** into the new repository folder, including dotfiles. `index.html` must sit directly at the repository root, beside `assets`, `docs`, and `VERSION`; avoid an extra nested project folder. On a Mac, **Command+Shift+.** shows hidden files in Finder.
4. In Desktop, review the changed files. Enter **`Initial SSC-NG demo (v0.1.0)`** as the commit summary and commit to `main`.
5. Click **Publish repository**. Select the intended account or organization and visibility, then publish. This is the step that sends your repository to GitHub.
6. On GitHub, optionally open **Releases → Draft a new release**, create the tag **`v0.1.0`** targeting the uploaded `main` commit, and add the corresponding changelog notes. Publish it when ready. Tag creation through this interface is sufficient for a named checkpoint; the command-line route below explicitly creates an annotated tag.

Desktop will show future edits in its **Changes** view and earlier commits in **History**. You can inspect a commit's diff to see exactly which source lines changed.

## Command-line alternative

Use this alternative instead of the Desktop initialization steps. Open a terminal in the extracted project folder. Replace the identity placeholders with your own details; the configuration below applies only to this repository.

```sh
git init -b main
git config user.name "YOUR NAME"
git config user.email "YOUR COMMIT EMAIL"
git add .
git status
git commit -m "Initial SSC-NG demo (v0.1.0)"
```

Review `git status` before committing so the selected files are the ones you intend to publish.

Next, [create a new repository on GitHub](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository). Create an **empty repository**: do not initialize it with a README, `.gitignore`, or license, because your local repository already has its own history.

Replace `YOUR-ACCOUNT` and the repository name in the URL below with the real destination. GitHub may ask you to authenticate when pushing.

```sh
git remote add origin https://github.com/YOUR-ACCOUNT/ssc-ng-demo.git
git push -u origin main
git tag -a v0.1.0 -m "SSC-NG meeting demo v0.1.0"
git push origin v0.1.0
```

The annotated tag names the initial website release. You can later create a GitHub Release using that existing tag and copy in the changelog notes. See [GitHub's release instructions](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository).

## Future changes and releases

For a change you want colleagues to review, create a feature branch from an up-to-date `main`, edit the files, inspect the diff, run the relevant checks, and commit. For example:

```sh
git switch main
git pull --ff-only
git switch -c feature/reviewer-notes
```

After editing:

```sh
git diff
git add index.html assets/app.js
git commit -m "Clarify reviewer notes in the submission workflow"
git push -u origin feature/reviewer-notes
```

Stage the files you actually changed; the paths above are an example. Open a pull request on GitHub, explain the change and how you checked it, and merge after review. GitHub Desktop supports the same sequence through **New branch**, commit, **Publish branch**, and **Create pull request**.

When a group of changes is ready to release:

1. Update `VERSION`, add a dated entry to `CHANGELOG.md`, and update any displayed website version, including the README. For example, use `0.1.1` for a small fix or `0.2.0` for a substantial new capability. Do not change fictional package versions solely to match the website version.
2. Commit these release changes and merge them into `main` through the same review process.
3. Fetch the merged commit, tag it, and push the new tag:

```sh
git switch main
git pull --ff-only
git tag -a v0.1.1 -m "SSC-NG meeting demo v0.1.1"
git push origin v0.1.1
```

4. Optionally publish a GitHub Release for `v0.1.1` using its changelog entry.

Use a fresh tag for each release. When GitHub Pages publishes from `main`, it follows changes to that branch; creating a tag alone does not select an older website version for Pages.

## Inspect or undo a change

These read-only commands help explain what changed:

```sh
git log --oneline --decorate
git show v0.1.0
git diff v0.1.0..main
```

To undo a shared change, prefer a **revert**: it adds another commit that reverses a selected change while preserving the original history. Perform it on a new branch and review it through a pull request. Avoid rewriting published history or force-pushing just to remove a mistake.

The demo's **Restore** button only selects fictional package data in browser memory. It does not revert this website's Git repository or install software.

## Optional GitHub Pages

In the GitHub repository, open **Settings → Pages**, choose **Deploy from a branch**, and select **main** with **/ (root)**. Save and use the URL GitHub provides after deployment completes. If Pages is unavailable, check the repository visibility and your account plan. [Official configuration instructions](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site).

No license is automatically assigned to this project. Agree on one with the team before adding a `LICENSE` file; the example package's license selector does not settle the website's license.
