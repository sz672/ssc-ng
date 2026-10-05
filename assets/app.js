/* SSC-NG submission demo. The local service owns checks, confirmation, review, and delivery. */
(() => {
  'use strict';
  const root = document.getElementById('sscng-meeting');
  const q = id => document.getElementById(id);
  if (window.location.protocol === 'file:') {
    q('sg-service-status').textContent = 'Open the local app';
    q('sg-stata-status').textContent = 'This HTML file is the interface only';
    root.querySelector('.sg-tabs').hidden = true;
    q('sg-submit').innerHTML = `<div class="sg-empty"><div class="sg-kicker">SSC-NG local pilot</div><h2>Open the working demo</h2><p>Package uploads, Stata checks, reviews, and deliveries run through the local service on your Mac.</p><a class="sg-link-button" href="http://127.0.0.1:8765/">Open SSC-NG on this Mac →</a><p>Start the service in the ssc-ng project folder:</p><pre>python3 server.py --port 8765</pre></div>`;
    return;
  }
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const tabs = ['submit', 'review', 'archive'];
  const fields = ['name','version','title','maintainer','email','stata','license','notes','source_url','test_file'];
  const API_REVISION = 1;
  const restartMessage = `This page needs a newer submission service at ${window.location.origin}. Restart its Python server with the same data directory. Keep this page open: your selected ZIP and entered details will stay here, and the connection will recover automatically.`;
  const state = {data:null,ready:false,serviceIssue:'Wait for the local service to connect before continuing.',tab:'submit',selectedSubmission:null,revision:null,busy:false,signature:'',drafts:{},comparisons:{},plans:{},mailbox:null,openDetails:new Set()};
  const label = value => ({queued:'Queued',running:'Running',passed:'Passed',failed:'Failed',unavailable:'Stata unavailable',changes_requested:'Changes requested',approved:'Approved',pending:'Pending',blocked:'Blocked',skipped:'Not run',pass:'Passed',fail:'Failed',verified:'Confirmed',expired:'Expired',locked:'Locked',delivering:'Delivering',delivered:'Delivered',superseded:'Superseded',warning:'Advisory'}[value] || value || 'Pending');
  const badge = (status, text) => `<span class="sg-badge ${['passed','approved','pass','verified','delivered'].includes(status) ? 'good' : ['failed','fail','locked'].includes(status) ? 'bad' : ['queued','running','unavailable','changes_requested','blocked','pending','skipped','expired','delivering','warning'].includes(status) ? 'warn' : ''}">${esc(text || label(status))}</span>`;
  const when = value => { const date = new Date(value); return !value ? 'Not recorded' : Number.isNaN(date.getTime()) ? esc(value) : esc(date.toLocaleString()); };
  const dependencies = metadata => (metadata.dependencies || []).map(dep => `${dep.name}@${dep.version}`).join(', ') || 'None';
  const metadataValue = value => value == null || value === '' ? '—' : Array.isArray(value) ? value.map(item => `${item.name}@${item.version}`).join(', ') || 'None' : String(value);
  const metadataLabel = key => ({name:'Package name',version:'Version',title:'Title',maintainer:'Maintainer',email:'Email',license:'License',notes:'Release notes',stata:'Minimum Stata',test_file:'Test file',source_url:'Project URL',dependencies:'Dependencies'}[key] || key);
  const selectedSubmission = () => (state.data?.submissions || []).find(item => item.id === state.selectedSubmission);
  const endpoint = (id, suffix) => `/api/submissions/${encodeURIComponent(id)}/${suffix}`;
  const draft = id => state.drafts[id] || {};
  const detail = (key, title, body) => `<details data-detail="${esc(key)}" ${state.openDetails.has(key) ? 'open' : ''}><summary>${title}</summary>${body}</details>`;
  const isPublished = item => item.delivery?.status === 'delivered' || Boolean(item.archive_files?.length);
  const isLegacyApproval = item => item.status === 'approved' && !item.delivery;

  function announce(message, error = false) {
    const el = q('sg-announcement');
    el.textContent = message; el.hidden = !message;
    el.className = error ? 'sg-error sg-announcement' : 'sg-toast sg-announcement';
  }

  function compatibilityError() {
    const error = new Error(restartMessage);
    error.code = 'API_INCOMPATIBLE';
    return error;
  }

  function serviceUnavailable(error) {
    state.ready = false;
    const incompatible = error.code === 'API_INCOMPATIBLE';
    state.serviceIssue = incompatible ? restartMessage : `Could not connect to the local submission service. Start it with python3 server.py, then open the address printed in the terminal. ${error.message}`;
    q('sg-service-status').textContent = incompatible ? 'Service restart required' : 'Service disconnected';
    q('sg-service-status').className = 'sg-badge bad';
    q('sg-stata-status').textContent = incompatible ? 'Page and service versions do not match' : 'Waiting for the local service';
    q('sg-connection-error').textContent = state.serviceIssue;
    q('sg-connection-error').hidden = false;
  }

  async function api(path, body) {
    if (body !== undefined && !state.ready) throw new Error(state.serviceIssue);
    const options = {headers:{Accept:'application/json'}};
    if (body !== undefined) { options.method = 'POST'; options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(body); }
    const response = await fetch(path, options).catch(error => { serviceUnavailable(error); throw error; });
    const data = await response.json().catch(() => ({error:`The local service returned HTTP ${response.status}.`}));
    if (response.status === 404 && path === '/api/intake/inspect') {
      const error = compatibilityError();
      serviceUnavailable(error);
      throw error;
    }
    if (!response.ok) throw new Error(data.error || `Request failed (HTTP ${response.status}).`);
    return data;
  }

  async function refresh(force = false) {
    try {
      const data = await api('/api/state');
      if (data.api_revision !== API_REVISION) throw compatibilityError();
      const previousIssue = state.serviceIssue;
      state.ready = true;
      state.serviceIssue = '';
      const recoveredIssues = [previousIssue, restartMessage].filter(Boolean);
      if (recoveredIssues.includes(q('sg-announcement').textContent)) announce('');
      if (recoveredIssues.includes(q('sg-form-error').textContent)) q('sg-form-error').hidden = true;
      q('sg-connection-error').hidden = true;
      q('sg-service-status').textContent = 'Local service online'; q('sg-service-status').className = 'sg-badge good';
      q('sg-stata-status').textContent = data.stata?.available ? 'Stata executable found' : 'Stata unavailable · checks cannot pass';
      q('sg-version-label').textContent = `SSC-NG ${data.version || ''}`;
      const signature = JSON.stringify(data), first = !state.data;
      state.data = data;
      if (!selectedSubmission()) state.selectedSubmission = data.submissions?.[0]?.id || null;
      if (first) renderExamples();
      if (force || signature !== state.signature) {
        if (signature !== state.signature) { state.comparisons = {}; state.plans = {}; }
        state.signature = signature;
        renderUpdates(); renderOwner();
        if (state.tab === 'review') renderReview();
        if (state.tab === 'archive') renderArchive();
      }
    } catch (error) {
      serviceUnavailable(error);
      if (force) throw error;
    }
  }

  function go(tab) {
    if (!tabs.includes(tab)) return;
    state.tab = tab;
    tabs.forEach(name => { q(`sg-${name}`).hidden = name !== tab; q(`sg-tab-${name}`).setAttribute('aria-selected', String(name === tab)); q(`sg-tab-${name}`).tabIndex = name === tab ? 0 : -1; });
    if (tab === 'review') renderReview();
    if (tab === 'archive') renderArchive();
  }

  function renderExamples() {
    const examples = state.data?.examples || [];
    q('sg-example').innerHTML = examples.map(example => `<option value="${esc(example.id)}">${esc(example.label)}</option>`).join('') || '<option>No examples available</option>';
    q('sg-example').disabled = !examples.length; q('sg-example-run').disabled = !examples.length; renderExampleDetail();
  }
  function renderExampleDetail() {
    const example = state.data?.examples?.find(item => item.id === q('sg-example').value);
    if (example) q('sg-example-detail').innerHTML = `<strong>${esc(example.metadata.name)} ${esc(example.metadata.version)}</strong><p>${esc(example.metadata.notes || example.metadata.title)}</p><span>Dependencies: ${esc(dependencies(example.metadata))}</span>`;
  }
  function renderUpdates() {
    const packages = state.data?.archive?.packages || [], previous = q('sg-update-package').value;
    q('sg-update-package').innerHTML = packages.map(item => `<option value="${esc(item.name)}">${esc(item.name)} · ${esc(item.version)}</option>`).join('') || '<option>No packages delivered yet</option>';
    if (packages.some(item => item.name === previous)) q('sg-update-package').value = previous;
    q('sg-update-package').disabled = !packages.length; q('sg-start-update').disabled = !packages.length;
  }
  function renderOwner() {
    const owner = (state.data?.owners || []).find(item => item.name === q('sg-name').value.trim());
    q('sg-owner-note').hidden = !owner;
    if (owner) q('sg-owner-note').textContent = `Existing package · recorded maintainer: ${owner.maintainer} (${owner.email}). Updates must use this maintainer’s email. Confirmation is simulated in the demo mailbox.`;
  }
  function setMetadata(m) {
    if (m.license && ![...q('sg-license').options].some(option => option.value === m.license)) q('sg-license').add(new Option(m.license, m.license));
    for (const name of fields) q(`sg-${name}`).value = m[name] ?? (name === 'test_file' ? 'smoke.do' : name === 'license' ? 'Unspecified' : '');
    q('sg-deps').value = (m.dependencies || []).map(dep => `${dep.name}@${dep.version}`).join('\n');
    renderOwner();
  }
  function metadata() {
    const result = {};
    for (const name of fields) result[name] = q(`sg-${name}`).value.trim();
    result.dependencies = q('sg-deps').value.split(/[\n,]+/).map(value => value.trim()).filter(Boolean).map(value => {
      const match = /^([a-z][a-z0-9_]*)@([0-9]+\.[0-9]+\.[0-9]+)$/.exec(value);
      if (!match) throw new Error('Write each dependency as package@version, for example sscng_helper@0.1.0.');
      return {name:match[1],version:match[2]};
    });
    return result;
  }
  function trustRequired() {
    if (!q('sg-trusted').checked) throw new Error('Confirm that you trust this package before allowing its Stata code to run on your Mac.');
  }
  function base64(file) {
    return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = () => reject(new Error('The ZIP file could not be read.')); reader.readAsDataURL(file); });
  }
  function uploadFile() {
    const file = q('sg-file').files[0];
    if (!file) throw new Error('Select a package ZIP first.');
    if (!file.name.toLowerCase().endsWith('.zip')) throw new Error('Select a ZIP archive.');
    if (file.size > 10 * 1024 * 1024) throw new Error('The ZIP archive exceeds the 10 MB limit.');
    return file;
  }
  async function inspectUpload() {
    const result = await api('/api/intake/inspect', {zip_base64:await base64(uploadFile())});
    const m = {...result.metadata};
    if (!m.name && result.suggested_name) m.name = result.suggested_name;
    setMetadata(m);
    const inventory = result.inventory || [];
    const fileNames = Array.isArray(inventory) ? inventory.map(file => typeof file === 'string' ? file : file.path) : inventory.files || [];
    const missing = result.missing_fields || [];
    q('sg-intake-result').innerHTML = `<div class="sg-note"><strong>Package details imported · not submitted</strong><p>${missing.length ? `Complete these details: ${esc(missing.join(', '))}.` : 'Review the imported details, add release notes if needed, then submit when ready.'}</p>${(result.checks || []).map(check => `<p>${badge(check.status)} ${esc(check.name)}: ${esc(check.detail)}${check.remedy ? `<br>${esc(check.remedy)}` : ''}</p>`).join('')}${fileNames.length ? `<details><summary>Package inventory (${fileNames.length})</summary><ul class="sg-list">${fileNames.map(file => `<li><code>${esc(file)}</code></li>`).join('')}</ul></details>` : ''}${result.sources ? `<details><summary>Where the details came from</summary><pre>${esc(typeof result.sources === 'string' ? result.sources : JSON.stringify(result.sources, null, 2))}</pre></details>` : ''}</div>`;
    q('sg-intake-result').hidden = false;
    announce('Package details read. Nothing was submitted and no package code was run.');
  }
  async function submitUpload() {
    trustRequired();
    if (!q('sg-submission-form').reportValidity()) return;
    const body = {metadata:metadata(),trusted:true};
    const reusable = state.revision && !isPublished(state.revision) && !isLegacyApproval(state.revision);
    if (q('sg-file').files[0]) body.zip_base64 = await base64(uploadFile());
    else if (reusable) body.source_submission_id = state.revision.id;
    else throw new Error('Select a package ZIP before submitting.');
    if (reusable) body.parent_id = state.revision.id;
    const result = await api('/api/submissions', body);
    state.selectedSubmission = result.submission.id;
    await refresh(true); go('review');
    announce('Submission saved. Confirm the maintainer using the demo mailbox while checks run.');
  }
  function revise(submission) {
    state.revision = submission;
    const published = isPublished(submission) || isLegacyApproval(submission);
    setMetadata(submission.metadata);
    if (published) {
      q('sg-version').value = ''; q('sg-notes').value = '';
      const owner = (state.data?.owners || []).find(item => item.name === submission.name);
      if (owner) { q('sg-maintainer').value = owner.maintainer; q('sg-email').value = owner.email; }
    }
    q('sg-file').value = ''; q('sg-trusted').checked = false; q('sg-intake-result').hidden = true;
    q('sg-form-title').textContent = published ? 'Submit a package update' : 'Revise this submission';
    q('sg-draft').textContent = published ? 'Package update' : 'Revised candidate';
    q('sg-revision-note').textContent = published
      ? `Based on ${isLegacyApproval(submission) ? 'earlier approved' : 'delivered'} release ${submission.name} ${submission.version}. Choose a new release version and upload its ZIP. The update will be compared with the current archive.`
      : `Revises ${submission.name} ${submission.version}. Upload a replacement ZIP, or leave it empty to reuse the saved source. Previous feedback stays attached; this revision needs new checks, confirmation, and review.`;
    q('sg-revision-note').hidden = false; q('sg-clear-form').hidden = false; q('sg-form-error').hidden = true;
    go('submit'); q(published ? 'sg-version' : 'sg-notes').focus();
  }
  function startUpdate(name) {
    const item = (state.data?.archive?.packages || []).find(pkg => pkg.name === name);
    if (!item) throw new Error('This package is no longer in the current archive. Refresh and choose it again.');
    const submission = state.data.submissions.find(candidate => candidate.id === item.submission_id);
    revise({...submission,...item,metadata:item.metadata || submission?.metadata || {name:item.name},delivery:{status:'delivered'}});
  }
  function clearForm() {
    state.revision = null; q('sg-submission-form').reset(); q('sg-trusted').checked = false;
    q('sg-form-title').textContent = 'Submit a Stata package'; q('sg-draft').textContent = 'New submission';
    for (const id of ['sg-revision-note','sg-clear-form','sg-form-error','sg-intake-result','sg-owner-note']) q(id).hidden = true;
  }
  function fileList(files) {
    if (!files?.length) return '<p class="sg-small">No file inventory available.</p>';
    return `<div class="sg-table-wrap"><table class="sg-table"><thead><tr><th>File</th><th>Bytes</th><th>SHA-256</th></tr></thead><tbody>${files.map(file => `<tr><td class="sg-file">${esc(file.path)}</td><td>${esc(file.size)}</td><td class="sg-file" title="${esc(file.sha256)}">${esc((file.sha256 || '').slice(0,12))}…</td></tr>`).join('')}</tbody></table></div>`;
  }
  function confirmationPanel(item) {
    const c = item.confirmation;
    if (!c) return '<p class="sg-small">This older submission has no maintainer confirmation record. A new revision uses the current confirmation workflow.</p>';
    const mailbox = (state.mailbox?.messages || []).filter(message => message.submission_id === item.id);
    return `<div class="sg-row sg-between"><h3>Maintainer confirmation</h3>${badge(c.status)}</div><p class="sg-small">${c.status === 'verified' ? `Confirmation recorded for ${esc(c.email)}.` : `A confirmation code is addressed to ${esc(c.email)} in the local demo mailbox.`}</p>
      ${c.status !== 'verified' && !item.superseded_by && !['approved','superseded'].includes(item.status) ? `<div class="sg-confirm-fields"><label>Confirmation code<input id="sg-confirm-code" autocomplete="one-time-code" autocapitalize="characters" spellcheck="false" value="${esc(draft(item.id).code || '')}" placeholder="Code from demo mailbox"></label><div class="sg-actions"><button type="button" data-action="confirm-maintainer" ${['locked','expired'].includes(c.status) ? 'disabled' : ''}>Confirm maintainer</button><button type="button" data-action="resend-confirmation">Get a new code</button></div></div><p class="sg-caption">${c.expires_at ? `Code expires ${when(c.expires_at)}.` : ''} Codes are specific to this submission.</p>` : ''}
      ${detail(`mailbox-${item.id}`, 'Demo mailbox — no email is sent', `<p class="sg-small">This visible mailbox simulates delivery for a walkthrough. It does not verify real email ownership. Read the code and enter it above.</p><button type="button" data-action="load-mailbox">Read demo mailbox</button>${state.mailbox ? mailbox.length ? mailbox.map(message => `<div class="sg-mail"><strong>${esc(message.subject)}</strong><div class="sg-small">To: ${esc(message.to)} · ${when(message.created_at)}</div><p>Confirmation code: <code class="sg-code">${esc(message.code)}</code></p><div class="sg-caption">Expires ${when(message.expires_at)}</div></div>`).join('') : '<p class="sg-small">No messages for this submission. Request a new code above.</p>' : ''}`)}`;
  }
  function comparisonPanel(item) {
    const comparison = state.comparisons[item.id];
    if (!comparison) return `<p class="sg-small">Inspect this candidate against the package currently in the archive. Revisions keep earlier review feedback.</p><button type="button" data-action="load-comparison">Show changes</button>`;
    const files = comparison.files || {};
    const paths = (items, kind) => (items || []).length ? `<h4>${kind} (${items.length})</h4><ul class="sg-list">${items.map(file => `<li><code>${esc(typeof file === 'string' ? file : file.path)}</code></li>`).join('')}</ul>` : '';
    return `<p class="sg-small">${comparison.base ? `Compared with ${esc(comparison.base.name)} ${esc(comparison.base.version)} in today’s archive.` : 'New package: no delivered version to compare.'}</p>${comparison.stale && !isPublished(item) && comparison.base?.submission_id !== item.id ? '<div class="sg-error">The current archive changed after this candidate was submitted. Create a revision against the new current package before delivery.</div>' : ''}
      ${comparison.metadata?.length ? `<div class="sg-table-wrap"><table class="sg-table sg-comparison-table"><thead><tr><th>Detail</th><th>Current</th><th>Candidate</th></tr></thead><tbody>${comparison.metadata.map(change => `<tr><td>${esc(metadataLabel(change.field))}</td><td>${esc(metadataValue(change.before))}</td><td>${esc(metadataValue(change.after))}</td></tr>`).join('')}</tbody></table></div>` : ''}
      ${paths(files.added, 'Added files')}${paths(files.removed, 'Removed files')}${(files.changed || []).map(file => detail(`diff-${item.id}-${file.path}`, `Changed: ${esc(file.path)}`, `<pre class="sg-log">${esc(file.diff || 'File contents differ. Download the candidate to inspect this file.')}</pre>`)).join('')}
      ${!(files.added?.length || files.removed?.length || files.changed?.length) ? '<p class="sg-small">No package file changes.</p>' : ''}<div class="sg-actions"><button type="button" data-action="load-comparison">Refresh comparison</button></div>`;
  }
  function lineage(item) {
    const ancestors = [], seen = new Set([item.id]);
    let parent = item.parent_id;
    while (parent && !seen.has(parent)) {
      seen.add(parent); const previous = state.data.submissions.find(candidate => candidate.id === parent);
      if (!previous) break;
      ancestors.push(previous); parent = previous.parent_id;
    }
    return ancestors.length ? `<div class="sg-panel sg-spaced"><h3>Earlier review feedback</h3>${ancestors.map(previous => `<div class="sg-evidence"><span>${esc(previous.name)} ${esc(previous.version)} · ${esc(label(previous.status))} · ${esc(String(previous.id).slice(0,8))}</span><p>${esc(previous.review_note || 'No reviewer note recorded.')}</p>${previous.reviewer ? `<div class="sg-small">Reviewer: ${esc(previous.reviewer)}</div>` : ''}<button type="button" data-submission="${esc(previous.id)}">View earlier submission</button></div>`).join('')}</div>` : '';
  }
  function renderReview() {
    const submissions = state.data?.submissions || [], item = selectedSubmission();
    if (!item) { q('sg-review').innerHTML = '<div class="sg-empty"><div class="sg-kicker">Reviewer workspace</div><h2>No submissions yet</h2><p>Upload a Stata package or run an included example to create the first check record.</p><button type="button" data-tab="submit">Submit a package</button></div>'; return; }
    const m = item.metadata, running = ['queued','running'].includes(item.status), superseded = Boolean(item.superseded_by) || item.status === 'superseded', terminal = superseded || ['approved','changes_requested'].includes(item.status), confirmed = item.confirmation?.status === 'verified';
    const checks = item.checks || [], fingerprint = item.checked_fingerprint || item.fingerprint;
    const approvalText = item.delivery?.status === 'delivered' ? 'Approved and delivered to today’s local archive.' : item.delivery ? 'Approved. Delivery is a separate step; the current archive changes only after a successful delivery.' : 'Existing approval record. No delivery is queued for this older record.';
    q('sg-review').innerHTML = `<div class="sg-intro"><div><div class="sg-kicker">Reviewer workspace</div><h2>Checks & human review</h2><p>Confirm the maintainer, inspect changes, and record a decision on this exact candidate.</p></div>${badge(superseded ? 'superseded' : item.status)}</div>
      <label class="sg-selector">Submission<select id="sg-submission-select">${submissions.map(submission => `<option value="${esc(submission.id)}" ${submission.id === item.id ? 'selected' : ''}>${esc(submission.name)} ${esc(submission.version)} · ${esc(label(submission.status))} · ${esc(String(submission.id).slice(0,8))}</option>`).join('')}</select></label>
      <div class="sg-grid"><div class="sg-stack"><div class="sg-panel"><div class="sg-row sg-between"><h3>Check results</h3>${running ? '<span class="sg-small">Updates automatically</span>' : ''}</div>
      ${checks.length ? checks.map(check => `<div class="sg-check"><div><strong>${esc(check.name)}</strong><div class="sg-small">${esc(check.detail)}</div>${check.remedy && !['pass','passed'].includes(check.status) ? `<p class="sg-remedy"><strong>Next step:</strong> ${esc(check.remedy)}</p>` : ''}${['warning','error'].includes(check.severity) ? `<div class="sg-caption">${esc(check.severity === 'warning' ? 'Advisory' : check.severity === 'error' ? 'Required check' : check.severity)}</div>` : ''}</div>${badge(check.status)}</div>`).join('') : `<p class="sg-small">${running ? 'Waiting for the local worker to report check results.' : 'This submission has no completed check results.'}</p>`}
      ${detail(`log-${item.id}`, 'Stata execution log', `<pre class="sg-log">${esc(item.log || (running ? 'The execution log will appear when the run completes.' : 'No Stata execution log was produced.'))}</pre>`)}
      ${detail(`files-${item.id}`, 'Submitted files and checked candidate', `${fileList(item.files)}${fingerprint ? `<p class="sg-caption">${item.checked_fingerprint ? 'Checked candidate' : 'Candidate'} fingerprint</p><code>${esc(fingerprint)}</code>` : ''}<p class="sg-caption">The fingerprint binds package files and submission details to this check and review record.</p>`)}
      <div class="sg-actions"><a class="sg-link-button" href="${endpoint(item.id, 'reproduce')}">Download check reproduction kit</a></div><p class="sg-caption">Includes the candidate and instructions for reproducing its checks locally.</p></div>
      <div class="sg-panel">${confirmationPanel(item)}</div>
      <div class="sg-panel"><h3>Candidate changes</h3>${comparisonPanel(item)}</div></div>
      <aside class="sg-panel"><h3>${esc(item.name)} ${esc(item.version)}</h3><p>${esc(m.title)}</p><div class="sg-evidence"><span>Release notes</span>${esc(m.notes)}</div><div class="sg-evidence"><span>Maintainer</span>${esc(m.maintainer)} · ${esc(m.email)}</div><div class="sg-evidence"><span>Requirements</span>Stata ${esc(m.stata)} · ${esc(m.license)}<br>Dependencies: ${esc(dependencies(m))}</div><div class="sg-evidence"><span>Test file</span><code>${esc(m.test_file || 'smoke.do')}</code></div>${m.source_url ? `<div class="sg-evidence"><span>Source URL</span>${esc(m.source_url)}</div>` : ''}<div class="sg-evidence"><span>Received</span>${when(item.created_at)}</div>${detail(`identity-${item.id}`, 'Submission identifiers', `<div class="sg-evidence"><span>Submission ID</span><code>${esc(item.id)}</code></div><div class="sg-evidence"><span>Bundle SHA-256</span><code>${esc(item.sha256)}</code></div>`)}<div class="sg-actions"><a class="sg-link-button" href="${endpoint(item.id, 'download')}">Download submitted ZIP</a></div></aside></div>
      ${lineage(item)}
      <div class="sg-panel sg-spaced"><h3>Review decision</h3>${item.review_note ? `<div class="sg-note"><strong>${item.reviewer ? `Review by ${esc(item.reviewer)}` : 'Saved reviewer note'}</strong><p>${esc(item.review_note)}</p></div>` : ''}
      ${!terminal ? `<label>Reviewer name<input id="sg-reviewer" autocomplete="name" value="${esc(draft(item.id).reviewer || '')}" placeholder="Who is recording this decision?"></label><label class="sg-spaced">Reviewer note<textarea id="sg-review-note" placeholder="Explain the approval or the changes needed">${esc(draft(item.id).note || '')}</textarea></label><div class="sg-actions"><button type="button" class="sg-primary" data-action="approve" ${item.status !== 'passed' || !confirmed ? 'disabled' : ''}>Approve submission</button><button type="button" data-action="request-changes" ${running ? 'disabled' : ''}>Request changes</button>${!running ? '<button type="button" data-action="revise">Create revised submission</button>' : ''}</div><p class="sg-caption">${item.status !== 'passed' ? 'Approval is blocked until this candidate passes all required checks.' : !confirmed ? 'Checks passed. Confirm the maintainer before approving this submission.' : 'Approval records the checked candidate and reviewer decision. Deliver it separately in the next step.'} Reviewer names are recorded locally; this demo has no reviewer login.</p>` : `<p>${item.status === 'approved' ? approvalText : superseded ? 'A newer revision supersedes this candidate. Its checks and review record remain available.' : 'Changes were requested. Create a revised submission and address the feedback above.'}</p><div class="sg-actions">${item.status === 'approved' ? '<button type="button" data-tab="archive">Open delivery & archive</button>' : ''}${item.superseded_by ? `<button type="button" data-submission="${esc(item.superseded_by)}">View newer revision</button>` : ''}${!superseded && item.delivery?.status !== 'delivering' ? `<button type="button" data-action="revise">${isPublished(item) || isLegacyApproval(item) ? 'Submit a package update' : 'Create revised submission'}</button>` : ''}</div>`}</div>`;
  }
  function archiveCommand(item) {
    const base = item.install_url || new URL(`/archive/${encodeURIComponent(item.name[0])}/`, window.location.href).href;
    return `net install ${item.name}, from("${base}") replace`;
  }
  function deliveryCard(item) {
    const d = item.delivery, receipt = d.receipt, plan = state.plans[item.id];
    return `<article class="sg-panel"><div class="sg-row sg-between"><h3>${esc(item.name)} <span class="sg-small">${esc(item.version)}</span></h3>${badge(d.status)}</div><p class="sg-small">Approved${item.reviewer ? ` by ${esc(item.reviewer)}` : ''} · candidate ${esc(String(item.id).slice(0,8))}</p>
      ${d.status === 'pending' ? '<p>Ready to deliver the reviewed files to today’s local archive.</p>' : d.status === 'delivering' ? '<p>Delivery is in progress. This status updates automatically.</p>' : d.status === 'failed' ? `<div class="sg-error">${esc(d.error || 'Delivery failed. The failure is recorded below.')}</div><p class="sg-caption">Retry a temporary destination failure. If the current package changed, create a revision for fresh checks and review.</p>` : `<p>Delivery completed ${when(receipt?.delivered_at)}.</p>`}
      ${plan ? `<div class="sg-note"><strong>Delivery preview</strong><p>${(plan.added || []).length} added · ${(plan.changed || []).length} changed · ${(plan.removed || []).length} removed</p>${(plan.blocking_reasons || []).map(reason => `<p class="sg-error">${esc(reason)}</p>`).join('')}${detail(`plan-${item.id}`, 'Files in this handoff', ['added','changed','removed','unchanged'].filter(kind => plan[kind]?.length).map(kind => `<h4>${esc(kind.charAt(0).toUpperCase() + kind.slice(1))}</h4><ul class="sg-list">${plan[kind].map(path => `<li><code>${esc(path)}</code></li>`).join('')}</ul>`).join(''))}</div>` : ''}
      ${d.attempts?.length ? detail(`attempts-${item.id}`, `Delivery attempts (${d.attempts.length})`, `<ul class="sg-list sg-attempts">${d.attempts.map(attempt => `<li><div>${badge(attempt.status)} <span class="sg-small">${when(attempt.at || attempt.started_at)}</span>${attempt.error ? `<p class="sg-small">${esc(attempt.error)}</p>` : ''}</div></li>`).join('')}</ul>`) : ''}
      ${receipt ? detail(`receipt-${item.id}`, 'Delivery receipt', `<div class="sg-evidence"><span>Receipt</span><code>${esc(receipt.id)}</code></div><div class="sg-evidence"><span>Destination</span><code>${esc(receipt.destination)}</code></div><div class="sg-evidence"><span>Reviewed fingerprint</span><code>${esc(receipt.fingerprint)}</code></div><ul class="sg-list">${(receipt.files || []).map(file => `<li><code>${esc(typeof file === 'string' ? file : file.path)}</code></li>`).join('')}</ul>`) : ''}
      <div class="sg-actions">${['pending','failed'].includes(d.status) ? `<button type="button" class="sg-primary" data-action="deliver" data-id="${esc(item.id)}">${d.status === 'failed' ? 'Retry delivery' : 'Deliver to today’s archive'}</button><button type="button" data-action="preview-delivery" data-id="${esc(item.id)}">Preview delivery</button>` : ''}<button type="button" data-submission="${esc(item.id)}">View review</button><a class="sg-link-button" href="${endpoint(item.id, 'handoff')}">Download handoff bundle</a>${d.status === 'delivered' ? `<a class="sg-link-button" href="${endpoint(item.id, 'receipt')}">Download receipt</a>` : ''}</div></article>`;
  }
  function renderArchive() {
    const archive = state.data?.archive || {}, handoff = state.data?.handoff || {}, packages = [...(archive.packages || [])].sort((a,b) => a.name.localeCompare(b.name));
    const deliveries = (state.data?.submissions || []).filter(item => item.status === 'approved' && item.delivery);
    const waiting = deliveries.filter(item => item.delivery.status !== 'delivered'), delivered = deliveries.filter(item => item.delivery.status === 'delivered');
    q('sg-archive').innerHTML = `<div class="sg-intro"><div><div class="sg-kicker">Submission destination</div><h2>Deliver & today’s archive</h2><p>Deliver approved candidates, track the outcome, and inspect the current accepted files.</p></div>${badge(waiting.length ? 'pending' : 'delivered', `${waiting.length} awaiting delivery`)}</div>
      <div class="sg-panel sg-handoff"><div><h3>Deliver reviewed files to the current archive</h3><p>Approval records the decision. Delivery writes the exact reviewed package files and returns a receipt. An update replaces the current package and removes its obsolete files.</p><p class="sg-small">Archive history and versioning are handled by the existing <a href="https://github.com/ssc-ng/archive/" target="_blank" rel="noopener noreferrer">ssc-ng/archive process</a>. This local demo is not connected to the production archive.</p>${detail('handoff-contract', 'Destination & handoff contract', `<div class="sg-evidence"><span>Local destination</span><code>${esc(handoff.root || archive.root || 'Configured by the local service')}</code></div><div class="sg-evidence"><span>Package layout</span><code>${esc(typeof handoff.layout === 'string' ? handoff.layout : '<first letter>/<package>.pkg and its listed files')}</code></div><p class="sg-small">The handoff bundle contains the approved inventory and package files, plus a manifest identifying the checked candidate. The current archive is served for Stata installation; its historical versioning remains with the established archive process.</p><p class="sg-small">The production destination and the operator who accepts this handoff still need to be agreed with the archive maintainers.</p>`)}</div>${badge('pending', 'Local handoff only')}</div>
      <div class="sg-section-head"><h3>Delivery queue</h3><span class="sg-small">${waiting.length} approved candidate${waiting.length === 1 ? '' : 's'}</span></div>
      ${waiting.length ? `<div class="sg-archive-grid">${waiting.map(deliveryCard).join('')}</div>` : '<div class="sg-empty"><p>No candidates waiting for delivery. Approved submissions appear here after checks and maintainer confirmation.</p><button type="button" data-tab="review">Open checks & review</button></div>'}
      ${delivered.length ? detail('completed-deliveries', `Completed deliveries (${delivered.length})`, `<div class="sg-archive-grid sg-spaced">${delivered.map(deliveryCard).join('')}</div>`) : ''}
      <div class="sg-section-head"><h3>Current packages</h3><span class="sg-small">${packages.length} package${packages.length === 1 ? '' : 's'}</span></div>
      ${packages.length ? `<div class="sg-archive-grid">${packages.map(item => `<article class="sg-panel"><div class="sg-row sg-between"><h3>${esc(item.name)} <span class="sg-small">${esc(item.version)}</span></h3>${badge('delivered', 'Current')}</div><p class="sg-small">Written ${when(item.published_at)}</p>${detail(`current-files-${item.name}`, `Current package files (${(item.files || []).length})`, `<ul class="sg-list">${(item.files || []).map(path => `<li><code>${esc(path)}</code></li>`).join('')}</ul>`)}${detail(`install-${item.name}`, 'Install from this local archive', `<pre>${esc(archiveCommand(item))}</pre><p class="sg-caption">Keep the local service running. Install any required dependencies first.</p>`)}<div class="sg-actions"><button type="button" data-action="update-package" data-name="${esc(item.name)}">Submit update</button><a class="sg-link-button" href="/api/archive/${encodeURIComponent(item.name)}/download">Download current ZIP</a>${item.submission_id ? `<button type="button" data-submission="${esc(item.submission_id)}">View submission</button>` : ''}</div></article>`).join('')}</div>` : '<div class="sg-empty"><h3>No accepted packages yet</h3><p>Approve and deliver a passing submission to add the first package to today’s archive.</p></div>'}`;
  }
  async function perform(action, button) {
    if (state.busy) return;
    state.busy = true; root.setAttribute('aria-busy','true');
    if (button) button.disabled = true;
    announce('');
    try { await action(button); }
    catch (error) { announce(error.message, true); }
    finally { state.busy = false; root.removeAttribute('aria-busy'); if (button?.isConnected) button.disabled = false; }
  }
  async function reviewDecision(decision) {
    const item = selectedSubmission(), note = q('sg-review-note').value.trim(), reviewer = q('sg-reviewer').value.trim();
    if (!reviewer) throw new Error('Enter the reviewer’s name before recording a decision.');
    if (!note) throw new Error('Add a reviewer note explaining the decision.');
    await api(endpoint(item.id, 'review'), {decision,note,reviewer});
    await refresh(true);
    announce(decision === 'approve' ? `${item.name} ${item.version} approved. Open delivery & archive to deliver the reviewed files.` : 'Changes requested. Create a revision to address the saved reviewer feedback.');
  }
  const actions = {
    'inspect-upload':inspectUpload,
    'start-update':() => startUpdate(q('sg-update-package').value),
    'update-package':button => startUpdate(button.dataset.name),
    'submit-example':async () => { trustRequired(); const result = await api(`/api/examples/${encodeURIComponent(q('sg-example').value)}/submit`, {trusted:true}); state.selectedSubmission = result.submission.id; await refresh(true); go('review'); announce('Example submitted. Confirm its maintainer with the demo mailbox while the real Stata checks run.'); },
    'load-mailbox':async () => { state.mailbox = await api('/api/demo-mailbox'); renderReview(); },
    'confirm-maintainer':async () => { const item = selectedSubmission(), code = q('sg-confirm-code').value.trim(); if (!code) throw new Error('Read the local demo mailbox and enter the confirmation code.'); try { await api(endpoint(item.id,'confirm'),{code}); } catch (error) { await refresh(true); throw error; } await refresh(true); announce('Demo maintainer confirmation recorded for this candidate.'); },
    'resend-confirmation':async () => { const item = selectedSubmission(); await api(endpoint(item.id,'confirmation'),{}); state.mailbox = null; state.drafts[item.id] = {...draft(item.id),code:''}; await refresh(true); announce('A new code is available in the demo mailbox. Earlier codes no longer work.'); },
    'load-comparison':async () => { const id = selectedSubmission().id; state.comparisons[id] = await api(endpoint(id,'comparison')); renderReview(); },
    'approve':() => reviewDecision('approve'),
    'request-changes':() => reviewDecision('request_changes'),
    'preview-delivery':async button => { state.plans[button.dataset.id] = await api(endpoint(button.dataset.id,'plan')); renderArchive(); },
    'deliver':async button => { const id = button.dataset.id; let result; try { result = await api(endpoint(id,'deliver'),{}); } catch (error) { await refresh(true); throw error; } await refresh(true); const delivery = result.submission?.delivery; if (delivery?.status === 'failed') throw new Error(delivery.error || 'Delivery failed. Review the recorded attempt and retry when the cause is resolved.'); announce(delivery?.status === 'delivered' ? 'Delivery completed. The current archive and delivery receipt are available below.' : 'Delivery is in progress. Its status will update here.'); },
    'revise':() => revise(selectedSubmission()),
    'clear-form':clearForm,
  };
  root.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button || button.disabled) return;
    if (button.dataset.tab) { announce(''); go(button.dataset.tab); return; }
    if (button.dataset.submission) { state.selectedSubmission = button.dataset.submission; go('review'); return; }
    if (actions[button.dataset.action]) perform(actions[button.dataset.action],button);
  });
  root.addEventListener('submit', event => {
    event.preventDefault();
    if (event.target.id === 'sg-submission-form') perform(async () => { q('sg-form-error').hidden = true; try { await submitUpload(); } catch (error) { q('sg-form-error').textContent = error.message; q('sg-form-error').hidden = false; throw error; } },event.submitter);
  });
  root.addEventListener('change', event => {
    if (event.target.id === 'sg-example') renderExampleDetail();
    if (event.target.id === 'sg-submission-select') { state.selectedSubmission = event.target.value; renderReview(); }
    if (event.target.id === 'sg-file') q('sg-intake-result').hidden = true;
  });
  root.addEventListener('input', event => {
    const field = {'sg-review-note':'note','sg-reviewer':'reviewer','sg-confirm-code':'code'}[event.target.id];
    if (field && state.selectedSubmission) state.drafts[state.selectedSubmission] = {...draft(state.selectedSubmission),[field]:event.target.value};
    if (event.target.id === 'sg-name') renderOwner();
  });
  root.addEventListener('toggle', event => {
    if (!event.target.isConnected) return;
    const key = event.target.dataset.detail;
    if (key) { if (event.target.open) state.openDetails.add(key); else state.openDetails.delete(key); }
  },true);
  root.querySelector('.sg-tabs').addEventListener('keydown', event => {
    if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault(); const index = tabs.indexOf(state.tab);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    go(tabs[next]); q(`sg-tab-${tabs[next]}`).focus();
  });
  async function poll() { await refresh(); window.setTimeout(poll, state.data?.submissions?.some(item => ['queued','running'].includes(item.status) || item.delivery?.status === 'delivering') ? 1500 : 7000); }
  poll();
})();
