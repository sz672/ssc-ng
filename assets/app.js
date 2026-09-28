/* SSC-NG local pilot. Records and check results come from the local service. */
(() => {
  'use strict';
  const root = document.getElementById('sscng-meeting');
  const q = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const tabs = ['submit', 'review', 'history', 'records'];
  const state = {data: null, tab: 'submit', selectedSubmission: null, selectedRelease: null, revision: null, busy: false, signature: '', notes: {}, transferDraft: {}, diff: null, restore: null};
  const label = value => ({queued:'Queued',running:'Running',passed:'Passed',failed:'Failed',unavailable:'Stata unavailable',changes_requested:'Changes requested',approved:'Approved',pending:'Pending',blocked:'Blocked',skipped:'Not run',pass:'Passed',fail:'Failed'}[value] || value || 'Pending');
  const badge = (status, text) => `<span class="sg-badge ${['passed','approved','pass'].includes(status) ? 'good' : ['failed','fail'].includes(status) ? 'bad' : ['queued','running','unavailable','changes_requested','blocked','pending','skipped'].includes(status) ? 'warn' : ''}">${esc(text || label(status))}</span>`;
  const when = value => { const date = new Date(value); return Number.isNaN(date.getTime()) ? esc(value) : esc(date.toLocaleString()); };
  const releaseKey = release => `${release.name}@${release.version}`;
  const endpoint = (name, version) => `/api/releases/${encodeURIComponent(name)}/${encodeURIComponent(version)}`;
  const dependencies = metadata => (metadata.dependencies || []).map(dep => `${dep.name}@${dep.version}`).join(', ') || 'None';
  const selectedSubmission = () => (state.data?.submissions || []).find(item => item.id === state.selectedSubmission);
  const selectedRelease = () => (state.data?.releases || []).find(item => releaseKey(item) === state.selectedRelease);
  const compareVersions = (a, b) => {
    const left = a.split('.').map(BigInt), right = b.split('.').map(BigInt);
    for (let i = 0; i < 3; i++) if (left[i] !== right[i]) return left[i] > right[i] ? 1 : -1;
    return 0;
  };

  function announce(message, error = false) {
    const el = q('sg-announcement');
    el.textContent = message;
    el.hidden = !message;
    el.className = error ? 'sg-error sg-announcement' : 'sg-toast sg-announcement';
  }

  async function api(path, body) {
    const options = {headers: {'Accept': 'application/json'}};
    if (body !== undefined) { options.method = 'POST'; options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(body); }
    const response = await fetch(path, options);
    const data = await response.json().catch(() => ({error: `The local service returned HTTP ${response.status}.`}));
    if (!response.ok) throw new Error(data.error || `Request failed (HTTP ${response.status}).`);
    return data;
  }

  async function refresh(force = false) {
    try {
      const data = await api('/api/state');
      data.releases.sort((a, b) => a.name.localeCompare(b.name) || compareVersions(b.version, a.version));
      q('sg-connection-error').hidden = true;
      q('sg-service-status').textContent = 'Local service online';
      q('sg-service-status').className = 'sg-badge good';
      q('sg-stata-status').textContent = data.stata?.available ? 'Stata executable found' : 'Stata unavailable · checks cannot pass';
      q('sg-version-label').textContent = `SSC-NG ${data.version || ''}`;
      const signature = JSON.stringify(data);
      const first = !state.data;
      state.data = data;
      if (!selectedSubmission()) state.selectedSubmission = data.submissions?.[0]?.id || null;
      if (!selectedRelease()) state.selectedRelease = data.releases?.[0] ? releaseKey(data.releases[0]) : null;
      if (first) renderExamples();
      if (force || signature !== state.signature) {
        state.signature = signature;
        if (state.tab === 'review') renderReview();
        if (state.tab === 'history') renderHistory();
        if (state.tab === 'records') renderRecords();
      }
    } catch (error) {
      q('sg-service-status').textContent = 'Service disconnected';
      q('sg-service-status').className = 'sg-badge bad';
      q('sg-connection-error').textContent = `Could not connect to the local registry. Start it with python3 server.py, then open the address printed in the terminal. ${error.message}`;
      q('sg-connection-error').hidden = false;
      if (force) throw error;
    }
  }

  function go(tab) {
    state.tab = tab;
    tabs.forEach(name => {
      q(`sg-${name}`).hidden = name !== tab;
      q(`sg-tab-${name}`).setAttribute('aria-selected', String(name === tab));
      q(`sg-tab-${name}`).tabIndex = name === tab ? 0 : -1;
    });
    if (tab === 'review') renderReview();
    if (tab === 'history') renderHistory();
    if (tab === 'records') renderRecords();
  }

  function renderExamples() {
    const examples = state.data?.examples || [];
    q('sg-example').innerHTML = examples.map(example => `<option value="${esc(example.id)}">${esc(example.label)}</option>`).join('') || '<option>No examples available</option>';
    q('sg-example').disabled = !examples.length;
    q('sg-example-run').disabled = !examples.length;
    renderExampleDetail();
  }

  function renderExampleDetail() {
    const example = state.data?.examples?.find(item => item.id === q('sg-example').value);
    if (!example) return;
    const m = example.metadata;
    q('sg-example-detail').innerHTML = `<strong>${esc(m.name)} ${esc(m.version)}</strong><p>${esc(m.notes || m.title)}</p><span>Dependencies: ${esc(dependencies(m))}</span>`;
  }

  function metadata() {
    const result = {};
    for (const name of ['name','version','title','maintainer','email','stata','license','notes']) result[name] = q(`sg-${name}`).value.trim();
    result.dependencies = q('sg-deps').value.split(/[\n,]+/).map(value => value.trim()).filter(Boolean).map(value => {
      const match = /^([a-z][a-z0-9_]*)@([0-9]+\.[0-9]+\.[0-9]+)$/.exec(value);
      if (!match) throw new Error('Write each dependency as package@version, for example sscng_helper@1.0.0.');
      return {name: match[1], version: match[2]};
    });
    return result;
  }

  function trustRequired(id = 'sg-trusted') {
    if (!q(id)?.checked) throw new Error('Confirm that you trust this package before allowing its Stata code to run on your Mac.');
  }

  function base64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',')[1]);
      reader.onerror = () => reject(new Error('The ZIP file could not be read.'));
      reader.readAsDataURL(file);
    });
  }

  async function submitUpload() {
    trustRequired();
    if (!q('sg-submission-form').reportValidity()) return;
    const body = {metadata: metadata(), trusted: true};
    const file = q('sg-file').files[0];
    if (file) {
      if (!file.name.toLowerCase().endsWith('.zip')) throw new Error('Select a ZIP archive.');
      if (file.size > 10 * 1024 * 1024) throw new Error('The ZIP archive exceeds the 10 MB limit.');
      body.zip_base64 = await base64(file);
    } else if (state.revision && state.revision.status !== 'approved') body.source_submission_id = state.revision.id;
    else throw new Error('Select a package ZIP before submitting.');
    if (state.revision && state.revision.status !== 'approved') body.parent_id = state.revision.id;
    const result = await api('/api/submissions', body);
    state.selectedSubmission = result.submission.id;
    await refresh(true);
    go('review');
    announce('Submission saved. The check queue will update here automatically.');
  }

  function revise(submission) {
    state.revision = submission;
    const m = submission.metadata;
    if (![...q('sg-license').options].some(option => option.value === m.license)) q('sg-license').add(new Option(m.license, m.license));
    for (const name of ['name','version','title','maintainer','email','stata','license','notes']) q(`sg-${name}`).value = m[name] ?? '';
    q('sg-deps').value = (m.dependencies || []).map(dep => `${dep.name}@${dep.version}`).join('\n');
    q('sg-file').value = '';
    q('sg-trusted').checked = false;
    q('sg-form-title').textContent = submission.status === 'approved' ? 'Prepare the next release' : 'Revise this submission';
    if (submission.status === 'approved') q('sg-version').value = '';
    q('sg-draft').textContent = 'New candidate';
    q('sg-revision-note').textContent = submission.status === 'approved'
      ? `Based on approved release ${submission.name} ${submission.version}. Choose a new release version and upload its ZIP. Earlier releases remain archived.`
      : `Based on ${submission.name} ${submission.version}. Choose a replacement ZIP, or leave it empty to reuse the saved source bundle. This creates a new immutable check record.`;
    q('sg-revision-note').hidden = false;
    q('sg-clear-form').hidden = false;
    q('sg-form-error').hidden = true;
    go('submit');
    q(submission.status === 'approved' ? 'sg-version' : 'sg-notes').focus();
  }

  function clearForm() {
    state.revision = null;
    q('sg-submission-form').reset();
    q('sg-trusted').checked = false;
    q('sg-form-title').textContent = 'Submit a Stata package';
    q('sg-draft').textContent = 'New submission';
    q('sg-revision-note').hidden = true;
    q('sg-clear-form').hidden = true;
    q('sg-form-error').hidden = true;
  }

  function fileList(files) {
    if (!files?.length) return '<p class="sg-small">No file inventory available.</p>';
    return `<div class="sg-table-wrap"><table class="sg-table"><thead><tr><th>File</th><th>Bytes</th><th>SHA-256</th></tr></thead><tbody>${files.map(file => `<tr><td class="sg-file">${esc(file.path)}</td><td>${esc(file.size)}</td><td class="sg-file" title="${esc(file.sha256)}">${esc((file.sha256 || '').slice(0,12))}…</td></tr>`).join('')}</tbody></table></div>`;
  }

  function renderReview() {
    const submissions = state.data?.submissions || [];
    const item = selectedSubmission();
    if (!item) { q('sg-review').innerHTML = '<div class="sg-empty"><div class="sg-kicker">Reviewer workspace</div><h2>No submissions yet</h2><p>Upload a Stata package or run an included example to create the first check record.</p><button type="button" data-tab="submit">Submit a package</button></div>'; return; }
    const m = item.metadata;
    const running = ['queued','running'].includes(item.status);
    const terminal = ['approved','changes_requested'].includes(item.status);
    const checks = item.checks || [];
    q('sg-review').innerHTML = `<div class="sg-intro"><div><div class="sg-kicker">Reviewer workspace</div><h2>Checks & human review</h2><p>Approval applies to the exact saved package and metadata.</p></div>${badge(item.status)}</div>
      <label class="sg-selector">Submission<select id="sg-submission-select">${submissions.map(submission => `<option value="${esc(submission.id)}" ${submission.id === item.id ? 'selected' : ''}>${esc(submission.name)} ${esc(submission.version)} · ${esc(label(submission.status))} · ${esc(String(submission.id).slice(0,8))}</option>`).join('')}</select></label>
      <div class="sg-grid"><div class="sg-panel"><div class="sg-row sg-between"><h3>Check results</h3>${running ? '<span class="sg-small">Updates automatically</span>' : ''}</div>
      ${checks.length ? checks.map(check => `<div class="sg-check"><div><strong>${esc(check.name)}</strong><div class="sg-small">${esc(check.detail)}</div></div>${badge(check.status)}</div>`).join('') : `<p class="sg-small">${running ? 'Waiting for the local worker to report check results.' : 'This submission has no completed check results.'}</p>`}
      <details ${item.status === 'failed' ? 'open' : ''}><summary>Stata execution log</summary><pre class="sg-log">${esc(item.log || (running ? 'The execution log will appear when the run completes.' : 'No Stata execution log was produced.'))}</pre></details>
      <details><summary>Submitted files</summary>${fileList(item.files)}</details></div>
      <aside class="sg-panel"><h3>${esc(item.name)} ${esc(item.version)}</h3><p>${esc(m.title)}</p><div class="sg-evidence"><span>Release notes</span>${esc(m.notes)}</div><div class="sg-evidence"><span>Maintainer</span>${esc(m.maintainer)} · ${esc(m.email)}</div><div class="sg-evidence"><span>Requirements</span>Stata ${esc(m.stata)} · ${esc(m.license)}<br>Dependencies: ${esc(dependencies(m))}</div><div class="sg-evidence"><span>Received</span>${when(item.created_at)}</div><div class="sg-evidence"><span>Submission ID</span><code>${esc(item.id)}</code></div><div class="sg-evidence"><span>Bundle SHA-256</span><code>${esc(item.sha256)}</code></div>${item.parent_id ? `<div class="sg-evidence"><span>Revises submission</span><code>${esc(item.parent_id)}</code></div>` : ''}<div class="sg-actions"><a class="sg-link-button" href="/api/submissions/${encodeURIComponent(item.id)}/download">Download submitted ZIP</a></div></aside></div>
      <div class="sg-panel sg-spaced"><h3>Review decision</h3>${item.review_note ? `<div class="sg-note">Saved reviewer note: ${esc(item.review_note)}</div>` : ''}
      ${!terminal ? `<label for="sg-review-note">Reviewer note</label><textarea id="sg-review-note" placeholder="Explain the approval or the changes needed">${esc(state.notes[item.id] || '')}</textarea><div class="sg-actions"><button type="button" class="sg-primary" data-action="approve" ${item.status !== 'passed' ? 'disabled' : ''}>Approve release</button><button type="button" data-action="request-changes" ${running ? 'disabled' : ''}>Request changes</button>${!running ? '<button type="button" data-action="revise">Create revised submission</button>' : ''}</div><p class="sg-caption">${item.status === 'passed' ? 'Review the checks and source before publishing to this local registry.' : 'Approval stays blocked until this exact candidate passes all required checks.'}</p>` : `<p>${item.status === 'approved' ? 'This release is archived in the local registry.' : 'Changes were requested. Create a revised submission and run its checks again.'}</p><div class="sg-actions">${item.status === 'approved' ? '<button type="button" data-action="view-release">View approved release</button>' : ''}<button type="button" data-action="revise">${item.status === 'approved' ? 'Prepare next release' : 'Create revised submission'}</button></div>`}</div>`;
  }

  function installCommands(item) {
    const base = new URL(item.install_url, window.location.href).origin;
    return [...(item.dependency_releases || []).map(dep => `net install ${dep.name}, from("${base}/packages/${dep.name}/${dep.version}/") replace`), `net install ${item.name}, from("${item.install_url}") replace`].join('\n');
  }

  function renderHistory() {
    const releases = state.data?.releases || [];
    const item = selectedRelease();
    if (!item) { q('sg-history').innerHTML = '<div class="sg-empty"><div class="sg-kicker">Package archive</div><h2>No approved releases yet</h2><p>Passing checks make a submission ready for review. Approve it to create an archived release and a working Stata installation URL.</p><button type="button" data-tab="review">Open checks & review</button></div>'; return; }
    const m = item.metadata;
    const transfers = (state.data.transfers || []).filter(transfer => transfer.name === item.name && transfer.status === 'approved').sort((a, b) => (b.approved_at || b.created_at).localeCompare(a.approved_at || a.created_at));
    const currentMetadata = releases.find(release => release.name === item.name).metadata;
    const owner = transfers[0] ? `${transfers[0].to_maintainer} · ${transfers[0].to_email}` : `${currentMetadata.maintainer} · ${currentMetadata.email}`;
    const environment = state.data.environment;
    q('sg-history').innerHTML = `<div class="sg-intro"><div><div class="sg-kicker">Package archive</div><h2>Approved releases</h2><p>Every approved bundle is retained with its checksums and check record.</p></div>${badge('approved', `${releases.length} release${releases.length === 1 ? '' : 's'}`)}</div>
      <div class="sg-release-grid"><aside><div class="sg-timeline">${releases.map(release => `<button type="button" class="sg-release" data-release="${esc(releaseKey(release))}" aria-pressed="${releaseKey(release) === state.selectedRelease}"><strong>${esc(release.name)} ${esc(release.version)}</strong><span class="sg-small">${when(release.created_at)}</span></button>`).join('')}</div><div class="sg-note">${environment ? `Managed local library: ${esc(environment.name)} ${esc(environment.version)}<br>${when(environment.installed_at)}${environment.library_path ? `<br><code>${esc(environment.library_path)}</code>` : ''}` : 'No release has been restored into the managed local library yet.'}</div></aside>
      <div class="sg-panel"><div class="sg-row sg-between"><h3>${esc(item.name)} ${esc(item.version)}</h3>${badge('approved','Archived')}</div><p>${esc(m.title)}</p><p>${esc(m.notes)}</p><div class="sg-note">Current recorded maintainer: ${esc(owner)}<br>Minimum Stata: ${esc(m.stata)} · License: ${esc(m.license)}<br>Dependencies: ${esc(dependencies(m))}</div><div class="sg-evidence"><span>Archived bundle SHA-256</span><code>${esc(item.sha256)}</code></div>${fileList(item.files)}
      <details open><summary>Install this release in Stata</summary><pre id="sg-install-command">${esc(installCommands(item))}</pre><p class="sg-caption">Keep the local service running while Stata installs. These URLs are reachable from this Mac. Run the commands in the order shown; dependencies are listed first.</p></details><div class="sg-actions"><a class="sg-link-button" href="${esc(item.download_url || endpoint(item.name,item.version) + '/download')}">Download release ZIP</a><button type="button" data-action="copy-install">Copy install command</button><button type="button" data-action="load-diff">Compare with previous release</button></div><div id="sg-diff-result">${state.diff?.key === state.selectedRelease ? diffHTML(state.diff.data) : ''}</div>
      <div class="sg-restore"><h3>Restore & verify this version</h3><p class="sg-small">Install this archived release and its dependencies into the pilot’s managed library, then run its saved smoke checks. Your normal Stata PLUS directory is unchanged.</p><label class="sg-checkbox"><input id="sg-restore-trusted" type="checkbox" ${state.restoreTrust === state.selectedRelease ? 'checked' : ''}><span>I trust this archived code and allow it to run on this Mac.</span></label><div class="sg-actions"><button type="button" data-action="restore">Restore ${esc(item.version)} & run checks</button></div>${state.restore?.key === state.selectedRelease ? `<div class="sg-note">${esc(state.restore.message)}</div><details open><summary>Restore log</summary><pre class="sg-log">${esc(state.restore.log)}</pre></details>` : ''}</div></div></div>`;
  }

  function diffHTML(data) {
    return `<div class="sg-diff"><h3>${data.previous_version ? `Changes from ${esc(data.previous_version)}` : 'First approved release'}</h3>${(data.added || []).map(path => `<div class="sg-diff-line sg-add">+ ${esc(path)}</div>`).join('')}${(data.removed || []).map(path => `<div class="sg-diff-line sg-remove">− ${esc(path)}</div>`).join('')}${(data.changed || []).map(change => `<details open><summary>${esc(change.path)}</summary><pre>${esc(change.diff || 'Binary file contents changed.')}</pre></details>`).join('')}${!(data.added?.length || data.removed?.length || data.changed?.length) ? '<p class="sg-small">No file content changes.</p>' : ''}</div>`;
  }

  function renderRecords() {
    const data = state.data || {};
    const names = [...new Set((data.releases || []).map(release => release.name))];
    q('sg-records').innerHTML = `<div class="sg-intro"><div><div class="sg-kicker">Registry records</div><h2>Captures & maintainer records</h2><p>These records describe the approved packages in this local registry.</p></div></div>
      <div class="sg-grid"><div class="sg-panel"><div class="sg-row sg-between"><h3>Catalog captures</h3><button type="button" data-action="capture">Capture current catalog</button></div><p class="sg-small">A capture records the current approved versions and bundle hashes. Unchanged bundles keep the same hash. A daily capture is also created while the service is running.</p>${data.snapshots?.length ? data.snapshots.map(snapshot => `<details><summary>${when(snapshot.created_at)} · ${snapshot.packages.length} package${snapshot.packages.length === 1 ? '' : 's'}</summary><ul class="sg-list">${snapshot.packages.map(pkg => `<li><span>${esc(pkg.name)} ${esc(pkg.version)}</span><code title="${esc(pkg.sha256)}">${esc(pkg.sha256.slice(0,12))}…</code></li>`).join('') || '<li>No approved packages at capture time.</li>'}</ul></details>`).join('') : '<div class="sg-note">No catalog captures yet.</div>'}</div>
      <div class="sg-panel"><h3>Record a maintainer transfer</h3><p class="sg-small">As the local operator, record the authorization you have checked. This pilot records your decision; it does not verify email ownership.</p><form id="sg-transfer-form"><div class="sg-fields"><label class="sg-full">Package<select id="sg-transfer-package" ${!names.length ? 'disabled' : ''}>${names.map(name => `<option>${esc(name)}</option>`).join('') || '<option>Approve a package first</option>'}</select></label><label>New maintainer<input id="sg-transfer-name" required maxlength="100"></label><label>New email<input id="sg-transfer-email" type="email" required maxlength="200"></label><label class="sg-full">Authorization evidence<textarea id="sg-transfer-evidence" required placeholder="Describe the prior maintainer’s authorization or the recovery evidence you reviewed."></textarea></label></div><div class="sg-actions"><button type="submit" data-action="request-transfer" ${!names.length ? 'disabled' : ''}>Save transfer request</button></div></form></div></div>
      <div class="sg-panel sg-spaced"><h3>Maintainer transfer decisions</h3>${data.transfers?.length ? data.transfers.map(transfer => `<div class="sg-transfer-record"><div class="sg-row sg-between"><strong>${esc(transfer.name)} · ${esc(transfer.from_maintainer)} → ${esc(transfer.to_maintainer)}</strong>${badge(transfer.status)}</div><p class="sg-small">${esc(transfer.to_email)} · ${when(transfer.created_at)}</p><p>${esc(transfer.evidence)}</p>${transfer.status === 'pending' ? `<button type="button" data-transfer="${esc(transfer.id)}">Approve recorded transfer</button>` : ''}</div>`).join('') : '<p class="sg-small">No maintainer transfers recorded.</p>'}</div>
      <div class="sg-panel sg-spaced"><h3>Activity record</h3>${data.events?.length ? `<ol class="sg-events">${data.events.map(event => `<li><span class="sg-small">${when(event.created_at)} · ${esc(event.kind)}</span><div>${esc(event.message)}</div></li>`).join('')}</ol>` : '<p class="sg-small">Activity will appear after the first submission.</p>'}</div>`;
    for (const [id, value] of Object.entries(state.transferDraft)) if (q(id)) q(id).value = value;
  }

  async function perform(action, button) {
    if (state.busy) return;
    state.busy = true;
    root.setAttribute('aria-busy','true');
    if (button) button.disabled = true;
    announce('');
    try { await action(); }
    catch (error) { announce(error.message, true); }
    finally { state.busy = false; root.removeAttribute('aria-busy'); if (button?.isConnected) button.disabled = false; }
  }

  const actions = {
    'submit-example': async () => {
      trustRequired();
      const result = await api(`/api/examples/${encodeURIComponent(q('sg-example').value)}/submit`, {trusted:true});
      state.selectedSubmission = result.submission.id;
      await refresh(true); go('review'); announce('Example submitted. These are real package checks on this Mac.');
    },
    'approve': async () => {
      const item = selectedSubmission();
      await api(`/api/submissions/${encodeURIComponent(item.id)}/review`, {decision:'approve',note:q('sg-review-note').value.trim()});
      state.selectedRelease = releaseKey(item); await refresh(true); announce(`${item.name} ${item.version} approved and archived in the local registry.`);
    },
    'request-changes': async () => {
      const item = selectedSubmission(); const note = q('sg-review-note').value.trim();
      if (!note) throw new Error('Add a reviewer note explaining what needs to change.');
      await api(`/api/submissions/${encodeURIComponent(item.id)}/review`, {decision:'request_changes',note});
      await refresh(true); announce('Changes requested. Submit a new revision to address the saved reviewer note.');
    },
    'revise': () => revise(selectedSubmission()),
    'clear-form': clearForm,
    'view-release': () => { state.selectedRelease = releaseKey(selectedSubmission()); go('history'); },
    'copy-install': async () => {
      const command = q('sg-install-command').textContent;
      try { await navigator.clipboard.writeText(command); announce('Stata install command copied.'); }
      catch (_) { announce('Select and copy the install command shown above.'); }
    },
    'load-diff': async () => {
      const item = selectedRelease(); const key = state.selectedRelease;
      state.diff = {key, data: await api(`${endpoint(item.name,item.version)}/diff`)};
      if (key === state.selectedRelease) q('sg-diff-result').innerHTML = diffHTML(state.diff.data);
    },
    'restore': async () => {
      trustRequired('sg-restore-trusted'); const item = selectedRelease(); const key = state.selectedRelease;
      announce('Restoring the archived bundle and running its Stata checks…');
      const result = await api(`${endpoint(item.name,item.version)}/restore`, {trusted:true});
      state.restore = {key,log:result.log || '',message:result.status === 'passed' ? 'Restore checks passed. This release is now in the managed local library.' : `Restore checks: ${label(result.status)}. Inspect the execution log.`};
      await refresh(true); announce(state.restore.message, result.status !== 'passed');
    },
    'capture': async () => { await api('/api/snapshots', {}); await refresh(true); announce('Current local catalog captured.'); },
  };

  root.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button || button.disabled) return;
    if (button.dataset.tab) { announce(''); go(button.dataset.tab); return; }
    if (button.dataset.release) { state.selectedRelease = button.dataset.release; state.diff = null; state.restore = null; state.restoreTrust = null; renderHistory(); return; }
    if (button.dataset.transfer) {
      perform(async () => { await api(`/api/transfers/${encodeURIComponent(button.dataset.transfer)}/approve`, {}); await refresh(true); announce('Maintainer transfer approved and recorded.'); }, button); return;
    }
    if (actions[button.dataset.action]) perform(actions[button.dataset.action], button);
  });
  root.addEventListener('submit', event => {
    event.preventDefault();
    if (event.target.id === 'sg-submission-form') {
      perform(async () => { q('sg-form-error').hidden = true; try { await submitUpload(); } catch (error) { q('sg-form-error').textContent = error.message; q('sg-form-error').hidden = false; throw error; } }, event.submitter);
    }
    if (event.target.id === 'sg-transfer-form') perform(async () => {
      await api('/api/transfers', {name:q('sg-transfer-package').value,to_maintainer:q('sg-transfer-name').value.trim(),to_email:q('sg-transfer-email').value.trim(),evidence:q('sg-transfer-evidence').value.trim()});
      state.transferDraft = {};
      await refresh(true); announce('Transfer request saved. Review its authorization evidence before approving.');
    }, event.submitter);
  });
  root.addEventListener('change', event => {
    if (event.target.id === 'sg-example') renderExampleDetail();
    if (event.target.id === 'sg-submission-select') { state.selectedSubmission = event.target.value; renderReview(); }
    if (event.target.id === 'sg-restore-trusted') state.restoreTrust = event.target.checked ? state.selectedRelease : null;
    if (event.target.id === 'sg-transfer-package') state.transferDraft[event.target.id] = event.target.value;
  });
  root.addEventListener('input', event => {
    if (event.target.id === 'sg-review-note' && state.selectedSubmission) state.notes[state.selectedSubmission] = event.target.value;
    if (event.target.closest('#sg-transfer-form')) state.transferDraft[event.target.id] = event.target.value;
  });
  root.querySelector('.sg-tabs').addEventListener('keydown', event => {
    if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault(); const index = tabs.indexOf(state.tab);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    go(tabs[next]); q(`sg-tab-${tabs[next]}`).focus();
  });
  async function poll() { await refresh(); window.setTimeout(poll, state.data?.submissions?.some(item => ['queued','running'].includes(item.status)) ? 1500 : 7000); }
  poll();
})();
