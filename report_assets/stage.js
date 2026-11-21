(() => {
  'use strict';

  /** @typedef {'blocker' | 'grade_impact' | 'advisory'} Severity */
  /**
   * @typedef {{
   *   author: string,
   *   reference: string,
   *   observation: string,
   *   evidence: readonly string[],
   * }} FindingSource
   */
  /** @typedef {{id: string, label: string, consequence: string}} DecisionOption */
  /**
   * @typedef {{
   *   id: string,
   *   topic: string,
   *   severity: Severity,
   *   status: string,
   *   title: string,
   *   submission: string,
   *   member: string | null,
   *   criterion: string | null,
   *   scenario: string | null,
   *   impact_percentage_points: number | null,
   *   summary: string,
   *   next_step: string,
   *   question: string,
   *   sources: readonly FindingSource[],
   *   options: readonly DecisionOption[],
   * }} Finding
   */
  /** @typedef {{issueId: string, optionId: string, note?: string}} Decision */
  /** @typedef {{title: string, summary: string, next_step: string}} GuideStop */
  /**
   * @typedef {{
   *   title: string,
   *   stage_label: string,
   *   stage: string,
   *   schema_version: number,
   *   run_id: string,
   *   review_digest: string,
   *   findings: readonly Finding[],
   *   stops: readonly GuideStop[],
   * }} StageReport
   */

  const reviewDigestPrefixLength = 12;
  const impactDecimalPlaces = 2;
  const reportData = document.getElementById('stage-data');
  const reportElement = document.getElementById('stage-app');
  if (!reportData?.textContent || !reportElement) return;
  const report = /** @type {StageReport} */ (JSON.parse(reportData.textContent));
  const reportRoot = reportElement;
  const severityNames = { blocker: 'Blocker', grade_impact: 'Grade impact', advisory: 'Advisory' };
  const findings = new Map(report.findings.map((finding) => [finding.id, finding]));
  /** @type {Map<string, Decision>} */
  const drafts = new Map();
  const topics = [...new Set(report.findings.map((finding) => finding.topic))].sort();
  const pointLabels = new Intl.NumberFormat('en-GB', { maximumFractionDigits: impactDecimalPlaces });
  /** @type {Readonly<Record<string, string>>} */
  const htmlEntities = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  /** @param {unknown} value */
  const escapeHtml = (value) =>
    String(value ?? '').replace(/[&<>"']/g, (character) => htmlEntities[character] ?? character);
  const findingQueue = document.createElement('div');
  const inspector = document.createElement('section');
  /** @type {string | undefined} */
  let selectedFindingId = report.findings[0]?.id;
  let topic = '';

  const currentFindings = () => report.findings.filter((finding) => !topic || finding.topic === topic);
  /** @param {Finding} finding */
  const impactLabel = (finding) =>
    finding.impact_percentage_points === null
      ? ''
      : `${pointLabels.format(finding.impact_percentage_points)} percentage points`;
  /** @param {Finding} finding */
  const severityBadge = (finding) =>
    `<span class="severity severity-${escapeHtml(finding.severity)}">${escapeHtml(severityNames[finding.severity])}</span>`;
  /** @param {string} reference */
  const evidenceLink = (reference) => {
    const [pathname] = reference.split('#');
    const segments = pathname.split('/');
    if (
      segments.some(
        (segment) => !/^[a-zA-Z0-9_.-]+$/.test(segment) || segment === '.' || segment === '..',
      )
    ) {
      return `<code>${escapeHtml(reference)}</code>`;
    }
    const path = segments.map((segment) => encodeURIComponent(segment)).join('/');
    return `<a href="../${escapeHtml(path)}"><code>${escapeHtml(reference)}</code></a>`;
  };

  function renderQueue() {
    findingQueue.innerHTML =
      currentFindings()
        .map(
          (finding) => `
      <button type="button" class="finding" data-finding="${escapeHtml(finding.id)}"
        aria-pressed="${String(selectedFindingId === finding.id)}">
        <span class="issue-meta">${severityBadge(finding)}<span class="muted">${escapeHtml(finding.topic)}</span></span>
        <strong>${escapeHtml(finding.title)}</strong>
        <span class="issue-meta muted"><span>${escapeHtml(finding.submission)}</span><span>${escapeHtml(impactLabel(finding))}</span>
          <span>${finding.sources.length} sources</span>${finding.status === 'reconciled' ? '<span>Reconciled</span>' : ''}
          ${drafts.has(finding.id) ? '<span>Decision drafted</span>' : ''}</span>
      </button>`,
        )
        .join('') || '<p class="muted">No findings in this view.</p>';
  }

  function renderInspector() {
    const finding = selectedFindingId ? findings.get(selectedFindingId) : undefined;
    inspector.hidden = !finding;
    if (!finding) return;
    const draft = drafts.get(finding.id);
    const scoped = [finding.submission, finding.member, finding.criterion, finding.scenario]
      .filter(Boolean)
      .join(' / ');
    const options = finding.options
      .map(
        (option) => `
      <label class="option"><input type="radio" name="decision-option" value="${escapeHtml(option.id)}"
        ${draft?.optionId === option.id ? 'checked' : ''}>
        <span>${escapeHtml(option.label)}<small class="muted">${escapeHtml(option.consequence)}</small></span>
      </label>`,
      )
      .join('');
    inspector.innerHTML = `
      <header>
        <p class="issue-meta muted">${severityBadge(finding)}<span>${escapeHtml(scoped)}</span></p>
        <h3>${escapeHtml(finding.title)}</h3>
      </header>
      <p>${escapeHtml(finding.summary)}</p>
      <p class="muted">${escapeHtml(finding.next_step)}</p>
      ${
        finding.sources.length
          ? `<details><summary>Evidence · ${finding.sources.length} sources</summary>
        <ul class="sources">${finding.sources
          .map(
            (source) => `<li><b>${escapeHtml(source.author)}</b> · ${escapeHtml(source.observation)}
          <small>${evidenceLink(source.reference)}</small>
          ${source.evidence.map((evidence) => `<small><code>${escapeHtml(evidence)}</code></small>`).join('')}</li>`,
          )
          .join('')}</ul>
      </details>`
          : ''
      }
      ${
        options
          ? `<fieldset class="options"><legend>${escapeHtml(finding.question)}</legend>${options}</fieldset>
        <label data-note ${draft?.optionId === 'custom' ? '' : 'hidden'}>Instructions for the next agent
          <textarea data-custom>${escapeHtml(draft?.note)}</textarea>
        </label>
        <div><button type="button" data-clear ${draft ? '' : 'disabled'}>Clear choice</button></div>`
          : ''
      }`;
  }

  function updateExport() {
    const decisions = [...drafts.values()].sort((left, right) =>
      left.issueId.localeCompare(right.issueId),
    );
    const incomplete = decisions.some(
      (decision) => decision.optionId === 'custom' && !decision.note?.trim(),
    );
    const exportable = decisions.length > 0 && !incomplete;
    copyButton.disabled = !exportable;
    downloadButton.disabled = !exportable;
    decisionJson.value = exportable
      ? JSON.stringify(
          {
            schemaVersion: report.schema_version,
            runId: report.run_id,
            stage: report.stage,
            reviewDigest: report.review_digest,
            decisions,
          },
          null,
          2,
        )
      : '';
    exportStatus.textContent = incomplete
      ? 'Add instructions for the custom choices.'
      : `${decisions.length} draft ${decisions.length === 1 ? 'decision' : 'decisions'}`;
  }

  reportRoot.innerHTML = `
    <header class="masthead">
      <div class="masthead-nav"><span class="eyebrow">Markwire · ${escapeHtml(report.stage_label)}</span>
        <a href="review.html">Open Columns ledger</a></div>
      <h1>${escapeHtml(report.title)}</h1>
      <p class="muted">Review revision <code>${escapeHtml(report.review_digest.slice(0, reviewDigestPrefixLength))}</code></p>
    </header>
    <section class="guide" aria-label="Stage guide"><h2>${escapeHtml(report.stage_label)} guide</h2>
      <div class="stops">${report.stops
        .map(
          (stop) => `<section class="stop"><h3>${escapeHtml(stop.title)}</h3>
        <p>${escapeHtml(stop.summary)}</p><p class="muted">${escapeHtml(stop.next_step)}</p></section>`,
        )
        .join('')}</div>
    </section>
    <section aria-labelledby="findings-title">
      <div class="findings-heading"><h2 id="findings-title">Findings</h2>
        ${
          topics.length > 1
            ? `<label>Topic <select data-topic><option value="">All topics</option>
          ${topics.map((item) => `<option>${escapeHtml(item)}</option>`).join('')}</select></label>`
            : ''
        }
      </div>
      <div class="review" data-review></div>
    </section>
    <section class="export" aria-label="Decision hand-off" ${report.findings.some((finding) => finding.options.length) ? '' : 'hidden'}>
      <div><h3>Decision hand-off</h3><p class="muted">Return the draft to the grading agent for validation and action.</p></div>
      <div class="actions">
        <button type="button" data-copy disabled>Copy JSON</button>
        <button type="button" data-download disabled>Download JSON</button>
        <span class="muted" data-status aria-live="polite"></span>
      </div>
      <textarea aria-label="Decision JSON" data-json readonly></textarea>
    </section>`;

  findingQueue.className = 'finding-queue';
  findingQueue.setAttribute('aria-label', 'Choose a finding');
  inspector.className = 'inspector';
  inspector.setAttribute('aria-label', 'Selected finding');
  const reviewContainer = reportRoot.querySelector('[data-review]');
  const copyControl = /** @type {HTMLButtonElement | null} */ (reportRoot.querySelector('[data-copy]'));
  const downloadControl = /** @type {HTMLButtonElement | null} */ (
    reportRoot.querySelector('[data-download]')
  );
  const jsonControl = /** @type {HTMLTextAreaElement | null} */ (
    reportRoot.querySelector('[data-json]')
  );
  const statusElement = reportRoot.querySelector('[data-status]');
  if (!reviewContainer || !copyControl || !downloadControl || !jsonControl || !statusElement) return;
  const copyButton = copyControl;
  const downloadButton = downloadControl;
  const decisionJson = jsonControl;
  const exportStatus = statusElement;
  reviewContainer.append(findingQueue, inspector);

  reportRoot.addEventListener('change', (event) => {
    const selection = /** @type {HTMLSelectElement | null} */ (event.target);
    if (selection?.matches?.('[data-topic]')) {
      topic = selection.value;
      const visibleFindings = currentFindings();
      selectedFindingId =
        visibleFindings.find((finding) => finding.id === selectedFindingId)?.id ??
        visibleFindings[0]?.id;
      renderQueue();
      renderInspector();
      return;
    }
    const choice = /** @type {HTMLInputElement | null} */ (event.target);
    if (!selectedFindingId || !choice?.matches?.('[name="decision-option"]')) return;
    const note = /** @type {HTMLTextAreaElement | null} */ (inspector.querySelector('[data-custom]'));
    const noteContainer = /** @type {HTMLElement | null} */ (inspector.querySelector('[data-note]'));
    const clearButton = /** @type {HTMLButtonElement | null} */ (
      inspector.querySelector('[data-clear]')
    );
    if (!note || !noteContainer || !clearButton) return;
    const custom = choice.value === 'custom';
    drafts.set(selectedFindingId, {
      issueId: selectedFindingId,
      optionId: choice.value,
      ...(custom ? { note: note.value } : {}),
    });
    noteContainer.hidden = !custom;
    clearButton.disabled = false;
    if (custom) note.focus();
    renderQueue();
    updateExport();
  });

  reportRoot.addEventListener('input', (event) => {
    const instructions = /** @type {HTMLTextAreaElement | null} */ (event.target);
    if (!selectedFindingId || !instructions?.matches?.('[data-custom]')) return;
    const draft = drafts.get(selectedFindingId);
    if (!draft) return;
    drafts.set(selectedFindingId, { ...draft, note: instructions.value });
    updateExport();
  });

  reportRoot.addEventListener('click', async (event) => {
    const target = /** @type {Element | null} */ (event.target);
    const button = target?.closest?.('button');
    if (!button) return;
    if (button.dataset.finding) {
      selectedFindingId = button.dataset.finding;
      renderQueue();
      renderInspector();
      /** @type {HTMLButtonElement | null} */ (
        findingQueue.querySelector('[aria-pressed="true"]')
      )?.focus();
    } else if (selectedFindingId && button.matches('[data-clear]')) {
      drafts.delete(selectedFindingId);
      renderQueue();
      renderInspector();
      /** @type {HTMLInputElement | null} */ (
        inspector.querySelector('[name="decision-option"]')
      )?.focus();
      updateExport();
    } else if (button.matches('[data-copy]')) {
      try {
        await navigator.clipboard.writeText(decisionJson.value);
        exportStatus.textContent = 'Decision JSON copied.';
      } catch {
        decisionJson.focus();
        decisionJson.select();
        exportStatus.textContent = 'Decision JSON selected for copying.';
      }
    } else if (button.matches('[data-download]')) {
      const serialisedDecisions = decisionJson.value;
      const download = document.createElement('a');
      download.href = URL.createObjectURL(
        new Blob([serialisedDecisions + '\n'], { type: 'application/json' }),
      );
      download.download = `decisions-${report.stage}-${report.review_digest.slice(0, reviewDigestPrefixLength)}.json`;
      download.click();
      setTimeout(() => URL.revokeObjectURL(download.href), 0);
      exportStatus.textContent = 'Decision JSON downloaded.';
    }
  });

  renderQueue();
  renderInspector();
  updateExport();
})();
