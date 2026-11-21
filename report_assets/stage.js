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
  /** @typedef {{id: string, label: string, consequence: string, owner?: string}} DecisionOption */
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
  /** @typedef {{optionId: string, note: string}} Draft */
  /** @typedef {{title: string, summary: string, next_step: string}} GuideStop */
  /** @typedef {{complete: number, total: number, label: string}} Progress */
  /**
   * @typedef {{
   *   title: string,
   *   stage_label: string,
   *   stage: string,
   *   schema_version: number,
   *   run_id: string,
   *   review_digest: string,
   *   progress?: Progress,
   *   findings: readonly Finding[],
   *   stops: readonly GuideStop[],
   * }} StageReport
   */
  /** @typedef {'needs-answer' | 'needs-note' | 'answered' | 'deferred' | 'reconciled' | 'informational'} AnswerState */
  /**
   * @typedef {{
   *   selectedFindingId: string | undefined,
   *   query: string,
   *   filter: string,
   *   topic: string,
   *   view: 'findings' | 'guide',
   *   previewOpen: boolean,
   * }} StageState
   */

  const digestPrefixLength = 12;
  const impactDecimalPlaces = 2;
  const customOptionId = 'custom';
  const deferOptionId = 'defer';
  const reportData = document.getElementById('stage-data');
  const reportElement = document.getElementById('stage-app');
  if (!reportData?.textContent || !reportElement) return;
  const reportRoot = reportElement;
  const report = /** @type {StageReport} */ (JSON.parse(reportData.textContent));
  const announcer = document.createElement('div');
  announcer.className = 'sr-only';
  announcer.setAttribute('aria-live', 'polite');
  reportRoot.after(announcer);

  /** @type {Readonly<Record<Severity, {label: string, glyph: string}>>} */
  const severities = {
    blocker: { label: 'Blocker', glyph: '■' },
    grade_impact: { label: 'Grade impact', glyph: '▲' },
    advisory: { label: 'Advisory', glyph: '●' },
  };
  /** @type {Readonly<Record<AnswerState, {label: string, glyph: string}>>} */
  const answerStates = {
    'needs-answer': { label: 'Needs answer', glyph: '○' },
    'needs-note': { label: 'Needs note', glyph: '✎' },
    answered: { label: 'Answered', glyph: '✓' },
    deferred: { label: 'Deferred', glyph: '‖' },
    reconciled: { label: 'Reconciled', glyph: '≡' },
    informational: { label: 'No decision', glyph: '–' },
  };
  /** @type {Readonly<Record<string, string>>} */
  const ownerNames = {
    ta: 'TA',
    setup: 'Setup',
    grader: 'Graders',
    arbiter: 'Arbiter',
    sanity: 'Sanity reviewer',
  };
  /** @type {Readonly<Record<string, string>>} */
  const htmlEntities = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  /** @param {unknown} value */
  const escapeHtml = (value) =>
    String(value ?? '').replace(/[&<>"']/g, (character) => htmlEntities[character] ?? character);
  const pointLabels = new Intl.NumberFormat('en-GB', { maximumFractionDigits: impactDecimalPlaces });

  const findings = new Map(report.findings.map((finding) => [finding.id, finding]));
  /** @param {Finding} finding */
  const isActionable = (finding) => finding.status === 'open' && finding.options.length > 0;
  const actionable = report.findings.filter(isActionable);
  const topics = [...new Set(report.findings.map((finding) => finding.topic))].sort();
  /** @type {Map<string, Draft>} */
  const drafts = new Map();
  /** @type {StageState} */
  const state = {
    selectedFindingId: report.findings[0]?.id,
    query: '',
    filter: 'all',
    topic: '',
    view: report.findings.length ? 'findings' : 'guide',
    previewOpen: false,
  };

  /**
   * @param {Finding} finding
   * @returns {AnswerState}
   */
  function answerState(finding) {
    if (!isActionable(finding)) return finding.status === 'reconciled' ? 'reconciled' : 'informational';
    const draft = drafts.get(finding.id);
    if (!draft) return 'needs-answer';
    if (draft.optionId === customOptionId && !draft.note.trim()) return 'needs-note';
    return draft.optionId === deferOptionId ? 'deferred' : 'answered';
  }
  /** @param {Finding} finding */
  const isRemaining = (finding) => {
    const answer = answerState(finding);
    return answer === 'needs-answer' || answer === 'needs-note';
  };
  const remainingFindings = () => actionable.filter(isRemaining);

  /** @type {Readonly<Record<string, {label: string, test: (finding: Finding) => boolean}>>} */
  const filters = {
    all: { label: 'All findings', test: () => true },
    remaining: { label: 'Needs answer', test: isRemaining },
    answered: { label: 'Answered', test: (finding) => answerState(finding) === 'answered' },
    deferred: { label: 'Deferred', test: (finding) => answerState(finding) === 'deferred' },
    blocker: { label: 'Blockers', test: (finding) => finding.severity === 'blocker' },
    grade_impact: { label: 'Grade impact', test: (finding) => finding.severity === 'grade_impact' },
    advisory: { label: 'Advisory', test: (finding) => finding.severity === 'advisory' },
    informational: { label: 'No decision needed', test: (finding) => !isActionable(finding) },
  };

  /** @param {Finding} finding */
  const matchesView = (finding) =>
    (filters[state.filter] ?? filters.all).test(finding) &&
    (!state.topic || finding.topic === state.topic) &&
    [
      finding.id,
      finding.title,
      finding.topic,
      finding.submission,
      finding.member,
      finding.criterion,
      finding.scenario,
      finding.summary,
    ]
      .join(' ')
      .toLowerCase()
      .includes(state.query.trim().toLowerCase());

  /** @param {Finding} finding */
  const impactLabel = (finding, short = false) =>
    finding.impact_percentage_points === null
      ? ''
      : `${pointLabels.format(finding.impact_percentage_points)}${short ? ' pp' : ' percentage points'}`;
  /** @param {Severity} severity */
  const severityTag = (severity) =>
    /* HTML */ `<span class="severity-tag severity-${escapeHtml(severity)}"
      ><i aria-hidden="true">${severities[severity]?.glyph ?? ''}</i
      >${escapeHtml(severities[severity]?.label ?? severity)}</span
    >`;
  /** @param {AnswerState} answer */
  const answerTag = (answer) =>
    /* HTML */ `<span class="answer-tag answer-${answer}"
      ><i aria-hidden="true">${answerStates[answer].glyph}</i>${answerStates[answer].label}</span
    >`;
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

  /** The single report-wide packet, or null until every actionable finding is answered. */
  function packet() {
    if (!actionable.length || remainingFindings().length) return null;
    /** @type {Decision[]} */
    const decisions = actionable
      .map((finding) => {
        const draft = /** @type {Draft} */ (drafts.get(finding.id));
        const note = draft.note.trim();
        return { issueId: finding.id, optionId: draft.optionId, ...(note ? { note } : {}) };
      })
      .sort((left, right) => (left.issueId < right.issueId ? -1 : left.issueId > right.issueId ? 1 : 0));
    return (
      JSON.stringify(
        {
          schemaVersion: report.schema_version,
          runId: report.run_id,
          stage: report.stage,
          reviewDigest: report.review_digest,
          decisions,
        },
        null,
        2,
      ) + '\n'
    );
  }

  function progressSummary() {
    const remaining = remainingFindings();
    const missingNotes = remaining.filter((finding) => answerState(finding) === 'needs-note').length;
    return {
      answered: actionable.length - remaining.length,
      total: actionable.length,
      remaining: remaining.length,
      missingNotes,
    };
  }

  /** @param {readonly Finding[]} visibleFindings */
  function renderQueue(visibleFindings) {
    const progress = progressSummary();
    const percentage = progress.total ? Math.round((progress.answered / progress.total) * 100) : 100;
    return /* HTML */ `<aside class="sidebar" aria-label="Finding queue">
      <div class="sidebar-head">
        <strong>Findings</strong
        ><span class="mono muted">${visibleFindings.length}/${report.findings.length}</span>
      </div>
      <div class="queue-controls">
        <label class="search-wrap"
          ><span class="search-icon" aria-hidden="true">⌕</span
          ><input
            type="search"
            data-search
            aria-label="Search findings"
            placeholder="Title, topic, submission"
            value="${escapeHtml(state.query)}"
        /></label>
        <div class="queue-tools">
          <select data-filter aria-label="Filter findings">
            ${Object.entries(filters)
              .map(
                ([id, filter]) =>
                  /* HTML */ `<option value="${id}" ${state.filter === id ? 'selected' : ''}>
                    ${filter.label} (${report.findings.filter(filter.test).length})
                  </option>`,
              )
              .join('')}
          </select>
          ${
            topics.length > 1
              ? /* HTML */ `<select data-topic aria-label="Filter by topic">
                  <option value="">All topics</option>
                  ${topics.map((topic) => /* HTML */ `<option ${state.topic === topic ? 'selected' : ''}>${escapeHtml(topic)}</option>`).join('')}
                </select>`
              : ''
          }
        </div>
      </div>
      ${
        progress.total
          ? /* HTML */ `<div
              class="answer-progress ${progress.remaining ? '' : 'is-complete'}"
              data-progress
            >
              <div class="answer-progress-head">
                <span>Decisions</span
                ><span class="mono"
                  ><b>${progress.answered}</b>/${progress.total}</span
                >
              </div>
              <div
                class="meter"
                role="progressbar"
                aria-label="Completed decisions across all topics"
                aria-valuemin="0"
                aria-valuemax="${progress.total}"
                aria-valuenow="${progress.answered}"
              >
                <span style="width:${percentage}%"></span>
              </div>
              <button type="button" class="next-button" data-next ${progress.remaining ? '' : 'disabled'}>
                ${progress.remaining ? `Next unanswered · ${progress.remaining} left` : 'All findings answered'}
              </button>
            </div>`
          : ''
      }
      <nav class="queue-list" data-scroll="queue" aria-label="Choose finding">
        ${
          visibleFindings
            .map((finding) => {
              const answer = answerState(finding);
              return /* HTML */ `<button
                type="button"
                class="queue-item severity-edge-${escapeHtml(finding.severity)} ${finding.id === state.selectedFindingId ? 'is-active' : ''}"
                data-finding="${escapeHtml(finding.id)}"
                aria-pressed="${finding.id === state.selectedFindingId}"
              >
                <span class="queue-top">${severityTag(finding.severity)}${answerTag(answer)}</span
                ><strong class="queue-name">${escapeHtml(finding.title)}</strong
                ><span class="queue-meta"
                  ><span>${escapeHtml(finding.topic)} · ${escapeHtml(finding.submission)}</span
                  ><span class="mono">${escapeHtml(impactLabel(finding, true))}</span></span
                >
              </button>`;
            })
            .join('') || '<p class="empty-state">No matching findings.</p>'
        }
      </nav>
      <div class="sidebar-footer">
        ${report.findings.length} ${report.findings.length === 1 ? 'finding' : 'findings'} · ${actionable.length} ${actionable.length === 1 ? 'needs' : 'need'} a decision
      </div>
    </aside>`;
  }

  /** @param {Finding} finding */
  function renderDetail(finding) {
    const scope = [
      ['Submission', finding.submission],
      ['Member', finding.member],
      ['Criterion', finding.criterion],
      ['Scenario', finding.scenario],
    ].filter(([, value]) => value);
    return /* HTML */ `<section class="ledger-panel" data-scroll="ledger" aria-label="Finding detail">
      <section class="sheet">
        <header class="sheet-heading">
          <span class="scope-code severity-code-${escapeHtml(finding.severity)}" aria-hidden="true"
            >${severities[finding.severity]?.glyph ?? ''}</span
          >
          <div class="sheet-label">
            <h2>${escapeHtml(finding.title)}</h2>
            <span>${escapeHtml(finding.topic)}</span>
          </div>
          ${severityTag(finding.severity)}
        </header>
        <dl class="scope-grid">
          ${scope.map(([label, value]) => /* HTML */ `<div><dt>${label}</dt><dd class="mono">${escapeHtml(value)}</dd></div>`).join('')}
          ${finding.impact_percentage_points === null ? '' : /* HTML */ `<div class="impact"><dt>Grade impact</dt><dd class="mono">${escapeHtml(impactLabel(finding))}</dd></div>`}
          <div><dt>Status</dt><dd>${answerTag(answerState(finding))}</dd></div>
        </dl>
      </section>
      <section class="overall-note">
        <div class="panel-label">Summary</div>
        <p class="overall-text feedback-text">${escapeHtml(finding.summary)}</p>
      </section>
      ${
        finding.next_step
          ? /* HTML */ `<section class="overall-note next-step">
              <div class="panel-label">Suggested next step</div>
              <p class="overall-text feedback-text">${escapeHtml(finding.next_step)}</p>
            </section>`
          : ''
      }
      <section class="sources" aria-label="Evidence">
        <div class="section-line">
          <h2>Evidence</h2>
          <span class="mono muted">${finding.sources.length} ${finding.sources.length === 1 ? 'source' : 'sources'}</span>
        </div>
        ${
          finding.sources
            .map(
              (source) => /* HTML */ `<article class="candidate-block">
                <div class="candidate-top">
                  <h3><span class="grader-code" aria-hidden="true">${escapeHtml(source.author.slice(0, 1).toUpperCase())}</span>${escapeHtml(source.author)}</h3>
                </div>
                <p class="feedback-text">${escapeHtml(source.observation)}</p>
                <div class="evidence-block">
                  ${evidenceLink(source.reference)}
                  ${source.evidence.map((evidence) => /* HTML */ `<code>${escapeHtml(evidence)}</code>`).join('')}
                </div>
              </article>`,
            )
            .join('') || '<p class="empty-state">No linked evidence.</p>'
        }
      </section>
    </section>`;
  }

  /** @param {Finding} finding */
  function renderDecision(finding) {
    const answer = answerState(finding);
    const header = /* HTML */ `<header class="inspector-header">
      <div class="panel-label">Decision ${answerTag(answer)}</div>
      <h2>${escapeHtml(isActionable(finding) ? finding.question : finding.title)}</h2>
    </header>`;
    if (!isActionable(finding)) {
      return /* HTML */ `<aside class="inspector" data-scroll="inspector" aria-label="Decision">
        ${header}
        <p class="empty-state">
          ${finding.status === 'reconciled' ? 'Already reconciled. No decision is needed for hand-off.' : 'For information only. No decision is needed for hand-off.'}
        </p>
      </aside>`;
    }
    const draft = drafts.get(finding.id);
    const custom = draft?.optionId === customOptionId;
    const progress = progressSummary();
    return /* HTML */ `<aside class="inspector" data-scroll="inspector" aria-label="Decision">
      ${header}
      <div class="inspector-content">
        <fieldset class="options">
          <legend class="sr-only">${escapeHtml(finding.question)}</legend>
          ${finding.options
            .map(
              (option) => /* HTML */ `<label
                class="option option-${option.id === deferOptionId ? 'defer' : option.id === customOptionId ? 'custom' : 'action'} ${draft?.optionId === option.id ? 'is-chosen' : ''}"
                ><input
                  type="radio"
                  name="decision-${escapeHtml(finding.id)}"
                  value="${escapeHtml(option.id)}"
                  data-option
                  ${draft?.optionId === option.id ? 'checked' : ''}
                /><span class="option-body"
                  ><span class="option-label">${escapeHtml(option.label)}</span
                  ><small>${escapeHtml(option.consequence)}</small
                  >${option.owner ? /* HTML */ `<small class="option-owner mono">→ ${escapeHtml(ownerNames[option.owner] ?? option.owner)}</small>` : ''}</span
                ></label
              >`,
            )
            .join('')}
        </fieldset>
        <label class="note-field ${custom && !draft?.note.trim() ? 'is-required' : ''}"
          ><span class="panel-label"
            >${custom ? 'Instructions for the next agent · required' : 'Note for the next agent · optional'}</span
          ><textarea
            data-note
            rows="3"
            ${draft ? '' : 'disabled'}
            ${custom ? 'required aria-required="true"' : ''}
            placeholder="${draft ? (custom ? 'Describe what should happen next (required)' : 'Add optional context for the next agent') : 'Select an option first'}"
          >
${escapeHtml(draft?.note ?? '')}</textarea
          ></label
        >
        <div class="decision-actions">
          <button type="button" class="quiet-button" data-clear ${draft ? '' : 'disabled'}>
            Clear answer
          </button>
          ${
            progress.remaining && !isRemaining(finding)
              ? /* HTML */ `<button type="button" class="next-button" data-next>
                  Next unanswered →
                </button>`
              : ''
          }
        </div>
      </div>
    </aside>`;
  }

  function renderGuide() {
    return /* HTML */ `<section class="guide-panel" data-scroll="guide" aria-label="Stage guide">
      <div class="section-line">
        <h2>${escapeHtml(report.stage_label)} guide</h2>
        <span class="muted">${report.stops.length} ${report.stops.length === 1 ? 'stop' : 'stops'}</span>
      </div>
      <ol class="stops">
        ${
          report.stops
            .map(
              (stop) => /* HTML */ `<li class="review-policy stop">
                <h3>${escapeHtml(stop.title)}</h3>
                <p>${escapeHtml(stop.summary)}</p>
                ${stop.next_step ? /* HTML */ `<p class="muted">${escapeHtml(stop.next_step)}</p>` : ''}
              </li>`,
            )
            .join('') || '<li class="empty-state">No guide stops.</li>'
        }
      </ol>
    </section>`;
  }

  function renderHandoff() {
    const progress = progressSummary();
    const json = packet();
    let message = 'No decisions are needed for this stage report.';
    if (progress.total && progress.remaining) {
      const findingWord = progress.remaining === 1 ? 'finding' : 'findings';
      const needWord = progress.remaining === 1 ? 'still needs' : 'still need';
      const noteDetail = progress.missingNotes
        ? ` (${progress.missingNotes} custom ${progress.missingNotes === 1 ? 'choice needs' : 'choices need'} instructions)`
        : '';
      message = `${progress.remaining} of ${progress.total} open ${findingWord} across all topics ${needWord} an answer${noteDetail}. Defer counts where offered; copy and download unlock when complete.`;
    } else if (json) {
      message = `All ${progress.total} ${progress.total === 1 ? 'finding is' : 'findings are'} answered. Copy or download this single JSON packet and import it with import-decisions.`;
    }
    return /* HTML */ `<section
      class="handoff ${json ? 'is-ready' : progress.total ? 'is-blocked' : ''}"
      aria-labelledby="handoff-title"
    >
      <div class="handoff-bar">
        <div class="handoff-summary">
          <h2 id="handoff-title">Decision hand-off</h2>
          <span class="handoff-count mono"
            >${progress.total ? `${progress.answered}/${progress.total}` : '0'}</span
          >
          <span class="handoff-message" id="handoff-message">${escapeHtml(message)}</span>
        </div>
        <div class="handoff-actions">
          ${progress.remaining ? '<button type="button" class="next-button" data-next>Next unanswered</button>' : ''}
          <button
            type="button"
            class="quiet-button"
            data-preview
            aria-expanded="${state.previewOpen}"
            aria-controls="handoff-json"
            ${json ? '' : 'disabled'}
          >
            ${state.previewOpen ? 'Hide JSON' : 'Show JSON'}
          </button>
          <button
            type="button"
            class="primary-button"
            data-copy
            aria-describedby="handoff-message"
            ${json ? '' : 'disabled'}
          >
            Copy JSON
          </button>
          <button
            type="button"
            class="primary-button"
            data-download
            aria-describedby="handoff-message"
            ${json ? '' : 'disabled'}
          >
            Download JSON
          </button>
        </div>
      </div>
      <textarea
        id="handoff-json"
        class="handoff-json"
        aria-label="Complete decision packet JSON"
        data-json
        readonly
        ${json && state.previewOpen ? '' : 'hidden'}
      >
${escapeHtml(json ?? '')}</textarea
      >
    </section>`;
  }

  function scrollPanels() {
    return /** @type {NodeListOf<HTMLElement>} */ (reportRoot.querySelectorAll('[data-scroll]'));
  }

  /** @param {string} selector */
  function control(selector) {
    return /** @type {HTMLElement | null} */ (reportRoot.querySelector(selector));
  }

  function render({ preserveScroll = true } = {}) {
    const scroll = Object.fromEntries(
      [...scrollPanels()].map((element) => [element.dataset.scroll ?? '', element.scrollTop]),
    );
    const visibleFindings = report.findings.filter(matchesView);
    if (visibleFindings.length && !visibleFindings.some((finding) => finding.id === state.selectedFindingId)) {
      state.selectedFindingId = visibleFindings[0].id;
      preserveScroll = false;
    }
    const finding = visibleFindings.find((item) => item.id === state.selectedFindingId);
    const progress = progressSummary();
    const severityCounts = /** @type {Severity[]} */ (Object.keys(severities))
      .map((severity) => [severity, report.findings.filter((item) => item.severity === severity).length])
      .filter(([, count]) => count);
    const readiness = !progress.total
      ? ['ready', '✓', 'No decisions needed']
      : progress.remaining
        ? progress.missingNotes === progress.remaining
          ? [
              'note',
              '✎',
              `${progress.missingNotes} ${progress.missingNotes === 1 ? 'needs' : 'need'} instructions`,
            ]
          : [
              'pending',
              '○',
              `${progress.remaining} ${progress.remaining === 1 ? 'answer' : 'answers'} needed`,
            ]
        : ['ready', '✓', 'Ready to hand off'];
    const stageProgress = report.progress;
    const body =
      state.view === 'guide'
        ? renderGuide()
        : finding
          ? renderDetail(finding) + renderDecision(finding)
          : /* HTML */ `<section class="empty-state report-empty">
              <h2>${report.findings.length ? 'No matching findings' : 'No findings for this stage'}</h2>
              <p>
                ${report.findings.length ? 'Change the search or filters to show a finding.' : 'Open the Stage guide tab to see what happens next.'}
              </p>
            </section>`;
    reportRoot.innerHTML = /* HTML */ `<div class="app-shell">
      <header class="app-bar">
        <span class="wordmark"><span class="brand-symbol" aria-hidden="true">m</span>Markwire</span
        ><span class="design-label">${escapeHtml(report.stage_label)}</span
        ><a class="ledger-link" href="review.html">Open Columns ledger</a
        ><span class="review-label">Staff stage report · offline</span>
      </header>
      <div class="workspace">
        ${renderQueue(visibleFindings)}
        <div class="main">
          <header class="submission-head">
            <div class="submission-title">
              <div class="submission-meta">
                <span class="mono">${escapeHtml(report.stage)}</span
                ><span title="${escapeHtml(report.review_digest)}"
                  >Revision
                  <span class="mono">${escapeHtml(report.review_digest.slice(0, digestPrefixLength))}</span></span
                ><span title="${escapeHtml(report.run_id)}"
                  >Run <span class="mono">${escapeHtml(report.run_id.slice(0, digestPrefixLength))}</span></span
                >
              </div>
              <h1>${escapeHtml(report.title)}</h1>
              <div class="identity-line">
                ${severityCounts.map(([severity, count]) => /* HTML */ `<span class="severity-count">${count} ${severityTag(/** @type {Severity} */ (severity))}</span>`).join('')}
              </div>
            </div>
            <div class="submission-state">
              <span class="status-badge status-${readiness[0]}"
                ><i aria-hidden="true">${readiness[1]}</i>${readiness[2]}</span
              >
              ${
                stageProgress
                  ? /* HTML */ `<span class="spread-summary"
                      ><span class="muted">${escapeHtml(stageProgress.label)}</span
                      ><b
                        class="mono ${stageProgress.complete >= stageProgress.total ? 'success' : 'info'}"
                        >${stageProgress.complete}/${stageProgress.total}</b
                      ></span
                    >`
                  : ''
              }
            </div>
          </header>
          <div class="report-toolbar">
            <nav class="view-tabs" aria-label="Report views">
              ${[
                ['findings', `Findings · ${report.findings.length}`],
                ['guide', `Stage guide · ${report.stops.length}`],
              ]
                .map(
                  ([id, label]) =>
                    /* HTML */ `<button
                      type="button"
                      data-view="${id}"
                      class="${state.view === id ? 'is-active' : ''}"
                      aria-pressed="${state.view === id}"
                    >
                      ${label}
                    </button>`,
                )
                .join('')}
            </nav>
            <span class="focus-label">${finding && state.view === 'findings' ? escapeHtml(finding.submission) : ''}</span>
          </div>
          <div class="report-body view-${state.view}">${body}</div>
          ${renderHandoff()}
        </div>
      </div>
      <footer class="status-bar">
        <span>${escapeHtml(report.stage_label)} · import the complete packet with the grading CLI</span
        ><span>${progress.answered}/${progress.total} answered · nothing is sent from this page</span>
      </footer>
    </div>`;
    if (preserveScroll) {
      for (const panel of scrollPanels()) {
        const position = scroll[panel.dataset.scroll ?? ''];
        if (position !== undefined) panel.scrollTop = position;
      }
    } else {
      const queuePosition = scroll.queue;
      const queue = control('[data-scroll="queue"]');
      if (queue && queuePosition !== undefined) queue.scrollTop = queuePosition;
    }
  }

  /** @param {string} message */
  function announce(message) {
    announcer.textContent = message;
  }

  function progressAnnouncement() {
    const progress = progressSummary();
    return progress.remaining
      ? `${progress.answered} of ${progress.total} answered. ${progress.remaining} remaining.`
      : `All ${progress.total} ${progress.total === 1 ? 'finding' : 'findings'} answered. The decision JSON packet is ready to copy or download.`;
  }

  /** Select the next finding that still needs an answer, revealing it if filters hide it. */
  function goToNextRemaining() {
    const remaining = remainingFindings();
    if (!remaining.length) return;
    const order = report.findings.filter(isActionable);
    const start = order.findIndex((finding) => finding.id === state.selectedFindingId);
    const target =
      order.slice(start + 1).find(isRemaining) ?? order.slice(0, start + 1).find(isRemaining);
    if (!target) return;
    let revealed = '';
    if (!matchesView(target)) {
      state.query = '';
      state.filter = 'all';
      state.topic = '';
      revealed = ' Filters cleared to show it.';
    }
    state.selectedFindingId = target.id;
    state.view = 'findings';
    render({ preserveScroll: false });
    control(`[data-finding="${target.id}"]`)?.scrollIntoView?.({ block: 'nearest' });
    control('[data-option]')?.focus({ preventScroll: true });
    announce(`${target.title}. ${answerStates[answerState(target)].label}.${revealed} ${progressAnnouncement()}`);
  }

  reportRoot.addEventListener('input', (event) => {
    const field = /** @type {HTMLInputElement | HTMLTextAreaElement | null} */ (event.target);
    if (field?.matches?.('[data-search]')) {
      const cursorPosition = field.selectionStart;
      state.query = field.value;
      render();
      const search = /** @type {HTMLInputElement | null} */ (control('[data-search]'));
      search?.focus();
      if (cursorPosition !== null) search?.setSelectionRange(cursorPosition, cursorPosition);
      const matchCount = report.findings.filter(matchesView).length;
      announce(`${matchCount} matching ${matchCount === 1 ? 'finding' : 'findings'}.`);
      return;
    }
    if (field?.matches?.('[data-note]') && state.selectedFindingId) {
      const draft = drafts.get(state.selectedFindingId);
      if (!draft) return;
      const before = answerState(/** @type {Finding} */ (findings.get(state.selectedFindingId)));
      const cursorPosition = field.selectionStart;
      const noteScroll = field.scrollTop;
      drafts.set(state.selectedFindingId, { ...draft, note: field.value });
      render();
      const note = /** @type {HTMLTextAreaElement | null} */ (control('[data-note]'));
      if (note) {
        note.focus({ preventScroll: true });
        if (cursorPosition !== null) note.setSelectionRange(cursorPosition, cursorPosition);
        note.scrollTop = noteScroll;
      }
      const after = answerState(/** @type {Finding} */ (findings.get(state.selectedFindingId)));
      if (before !== after) announce(`${answerStates[after].label}. ${progressAnnouncement()}`);
    }
  });

  reportRoot.addEventListener('change', (event) => {
    const field = /** @type {HTMLInputElement | HTMLSelectElement | null} */ (event.target);
    if (field?.matches?.('[data-filter]') || field?.matches?.('[data-topic]')) {
      const selector = field.matches('[data-filter]') ? '[data-filter]' : '[data-topic]';
      if (selector === '[data-filter]') state.filter = field.value;
      else state.topic = field.value;
      render();
      control(selector)?.focus();
      const matchCount = report.findings.filter(matchesView).length;
      announce(`${matchCount} matching ${matchCount === 1 ? 'finding' : 'findings'}.`);
      return;
    }
    if (field?.matches?.('[data-option]') && state.selectedFindingId) {
      const finding = findings.get(state.selectedFindingId);
      if (!finding || !finding.options.some((option) => option.id === field.value)) return;
      const previous = drafts.get(finding.id);
      drafts.set(finding.id, { optionId: field.value, note: previous?.note ?? '' });
      render();
      const custom = field.value === customOptionId && !previous?.note.trim();
      (custom
        ? control('[data-note]')
        : control(`[data-option][value="${CSS.escape(field.value)}"]`)
      )?.focus({ preventScroll: true });
      announce(
        `${answerStates[answerState(finding)].label}.${custom ? ' Add instructions for the next agent.' : ''} ${progressAnnouncement()}`,
      );
    }
  });

  reportRoot.addEventListener('click', async (event) => {
    const target = /** @type {Element | null} */ (event.target);
    const button = target?.closest?.('button');
    if (!button || button.disabled) return;
    const { dataset } = button;
    if (dataset.finding) {
      state.selectedFindingId = dataset.finding;
      state.view = 'findings';
      render({ preserveScroll: false });
      control(`[data-finding="${CSS.escape(dataset.finding)}"]`)?.focus({ preventScroll: true });
      const finding = findings.get(dataset.finding);
      if (finding) announce(`${finding.title}. ${answerStates[answerState(finding)].label}.`);
    } else if (dataset.view === 'findings' || dataset.view === 'guide') {
      state.view = dataset.view;
      render();
      control(`[data-view="${state.view}"]`)?.focus({ preventScroll: true });
    } else if ('next' in dataset) {
      goToNextRemaining();
    } else if ('clear' in dataset && state.selectedFindingId) {
      drafts.delete(state.selectedFindingId);
      render();
      control('[data-option]')?.focus({ preventScroll: true });
      announce(`Answer cleared. ${progressAnnouncement()}`);
    } else if ('preview' in dataset) {
      state.previewOpen = !state.previewOpen;
      render();
      control('[data-preview]')?.focus({ preventScroll: true });
    } else if ('copy' in dataset) {
      const json = packet();
      if (!json) return;
      try {
        await navigator.clipboard.writeText(json);
        announce('Decision JSON copied.');
      } catch {
        state.previewOpen = true;
        render();
        const output = /** @type {HTMLTextAreaElement | null} */ (control('[data-json]'));
        output?.focus();
        output?.select();
        announce('Clipboard access is blocked. The decision JSON is selected; press Ctrl+C or Cmd+C to copy it.');
      }
    } else if ('download' in dataset) {
      const json = packet();
      if (!json) return;
      const download = document.createElement('a');
      download.href = URL.createObjectURL(new Blob([json], { type: 'application/json' }));
      download.download = `decisions-${report.stage}-${report.review_digest.slice(0, digestPrefixLength)}.json`;
      download.click();
      setTimeout(() => URL.revokeObjectURL(download.href), 0);
      announce('Decision JSON downloaded.');
    }
  });

  render();
})();
