# SSC-NG meeting demo

Version **0.1.0**. An interactive prototype for discussing a future Stata package submission and review portal.

The demo includes three fictional cases:

- A routine package update, with simulated checks and human approval.
- A missing dependency, followed by a revised submission and another check.
- A maintainer transfer that preserves the published package files and releases.

The package history screen illustrates archived releases, file differences, shared bundles across unchanged daily captures, and selecting an earlier release.

## Run locally

Unzip the project and open **`index.html`** in your browser. No build or installation is needed.

Alternatively, run this command from the project folder:

```sh
python3 -m http.server 8000
```

Then open [http://localhost:8000](http://localhost:8000). Stop the server with **Ctrl+C**.

## Suggested meeting walkthrough

1. Choose **Routine update**, run the demo checks, approve the release, and inspect its package history.
2. Choose **Missing dependency**, inspect the failure, add the missing dependency, and rerun the checks before approval.
3. Choose **Maintainer transfer**, simulate prior-owner confirmation, approve the transfer, and verify that the release history remains intact.

Changing cases starts a fresh example. Reloading also resets the demo.

## Scope

All names, package files, version records, identities, logs, and endpoints are fictional. There is no backend, authentication, file upload, Stata execution, package installation, or real publication. Buttons change browser memory only. The prototype demonstrates proposed workflows; it does not implement a package registry or production security controls.

Git history for this website is separate from the simulated Stata package history shown inside it. See [Version control](docs/VERSION_CONTROL.md).

## Project files

| Path | Purpose |
| --- | --- |
| `index.html` | Page structure and entry point |
| `assets/styles.css` | Visual styling |
| `assets/app.js` | Demo state and interactions |
| `VERSION` | Website release version |
| `CHANGELOG.md` | Website release notes |
| `docs/VERSION_CONTROL.md` | GitHub setup and update instructions |
| `docs/MEETING_WALKTHROUGH.md` | Meeting cases, decision prompts, and pilot measures |
| `tests/smoke.cjs` | Optional browser smoke checks |

Keep these files together when copying or uploading the project.

## Put it on GitHub

For a first upload, use GitHub in your browser:

1. Create an empty repository under your account; leave the README, `.gitignore`, and license initialization options unchecked.
2. Choose **uploading an existing file** on the empty repository page, or **Add file → Upload files** if available.
3. Upload the extracted project contents, including dotfiles, with `index.html` at the repository root. On a Mac, **Command+Shift+.** shows hidden files in Finder.
4. Commit with the message **`Initial SSC-NG demo (v0.1.0)`**.
5. Optionally create a GitHub Release tagged **`v0.1.0`** from that commit.

Upload the files, not the ZIP itself. This ZIP contains no `.git` directory and creates no GitHub repository automatically. [Version control](docs/VERSION_CONTROL.md) explains this route, GitHub Desktop for local editing, and a command-line alternative.

To optionally publish the website with **GitHub Pages**, open the repository's **Settings → Pages**, select **Deploy from a branch**, then choose **main** and **/ (root)**. Save and wait for GitHub to show the site URL. Pages availability depends on your repository visibility and account plan. See [GitHub's publishing-source instructions](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site).

## Optional browser checks

Running the demo does not require Node.js. To run the smoke checks, install Node.js and npm, then run these commands from the project folder:

```sh
npm install playwright --no-save
npx playwright install chromium
node tests/smoke.cjs
```

If you already have a compatible Chromium executable, you can provide its path instead of downloading a browser. On macOS or Linux:

```sh
CHROMIUM_EXECUTABLE="/absolute/path/to/chromium" node tests/smoke.cjs
```

The checks exercise this browser prototype; they do not run Stata or validate real package submissions.

## License decision

No project license has been selected automatically. Agree on a license with the project team before inviting reuse, and add the corresponding `LICENSE` file. License choices displayed in the fictional submission form apply only to the example package; they do not license this website's source code.
