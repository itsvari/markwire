(() => {
  'use strict';
  /** @typedef {{raw: number, weighted: number}} GradeTotals */
  /** @typedef {{id: string, name: string, github: string, totals: Record<string, GradeTotals | null>, proposed: GradeTotals | null}} Member */
  /** @typedef {{score: number, feedback: string, evidence: readonly string[]}} Assessment */
  /** @typedef {Assessment & {rationale: string}} Verdict */
  /** @typedef {{id: string, scope: 'shared' | 'individual', member: string | null, name: string, max: number, weight: number, different: boolean, disputed: boolean, spread: number | null, candidates: Record<string, Assessment | null>, proposal: Verdict | null}} RubricRow */
  /** @typedef {'blocker' | 'grade_impact' | 'advisory'} Severity */
  /** @typedef {{author: string, observation: string, reference: string}} FindingSource */
  /**
   * @typedef {{
   *   topic: string,
   *   severity: Severity,
   *   title: string,
   *   impact: number | null,
   *   status: string,
   *   sources: readonly FindingSource[],
   *   summary: string,
   * }} Finding
   */
  /**
   * @typedef {{
   *   claim: string,
   *   status: string,
   *   finding: string,
   *   evidence: readonly string[],
   * }} VerificationCheck
   */
  /** @typedef {{reason: string, evidence: readonly string[]}} Correction */
  /** @typedef {{id: string, type: 'group' | 'individual', members: readonly Member[], rows: readonly RubricRow[], approved: boolean, ready: boolean, missing: readonly string[], flags: readonly string[], findings?: readonly Finding[], spread: number | null, disputed: boolean, summary: string | null, overall: Record<string, string | null>, source: {repo: string, branch: string, commit: string}, verification: {checks: readonly VerificationCheck[]} | null, verificationCurrent: boolean, history: readonly Correction[]}} Submission */
  /** @typedef {{id: string, code: string, name: string}} Grader */
  /**
   * @typedef {{
   *   submissions: readonly Submission[],
   *   graders: readonly Grader[],
   *   maximum: number,
   *   title: string,
   *   thresholds: {criterion_spread: number,
   *   total_spread: number},
   *   stageGuides?: readonly {stage: string,
   *   href: string}[],
   * }} Report
   */
  /**
   * @typedef {{
   *   selectedSubmissionId: string | undefined,
   *   selectedMemberId: string | null,
   *   selectedRowId: string | null,
   *   query: string,
   *   filter: string,
   *   view: string,
   *   differencesOnly: boolean,
   * }} LedgerState
   */

  const tableBaseWidthPixels = 390;
  const graderColumnWidthPixels = 65;
  const sourceDigestPrefixLength = 12;
  const impactDecimalPlaces = 2;
  const reportData = document.getElementById('report-data');
  const ledgerElement = document.getElementById('app');
  const announcementElement = document.getElementById('announcer');
  if (!reportData?.textContent || !ledgerElement || !announcementElement) return;
  const reportRoot = ledgerElement;
  const announcer = announcementElement;

  const report = /** @type {Report} */ (JSON.parse(reportData.textContent));
  const { submissions, graders, maximum, title, thresholds, stageGuides = [] } = report;
  const initialSubmissionId = new URL(location.href).searchParams.get('submission');
  /** @type {LedgerState} */
  const state = {
    selectedSubmissionId:
      submissions.find((submission) => submission.id === initialSubmissionId)?.id ?? submissions[0]?.id,
    selectedMemberId: null,
    selectedRowId: null,
    query: '',
    filter: 'all',
    view: 'scores',
    differencesOnly: false,
  };
  /** @type {Readonly<Record<string, string>>} */
  const htmlEntities = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  /** @param {unknown} value */
  const escapeHtml = (value) =>
    String(value ?? '').replace(/[&<>"']/g, (character) => htmlEntities[character] ?? character);
  /** @param {number | null} value */
  const formatScore = (value) =>
    value === null ? '<span class="unavailable">—</span>' : escapeHtml(value);
  /** @param {Member} member */
  const githubHandle = (member) =>
    member.github ? /* HTML */ `<span class="handle">@${escapeHtml(member.github)}</span>` : '';
  /** @param {Member} member */
  const memberIdentity = (member) =>
    /* HTML */ `<span class="person-name">${escapeHtml(member.name)}</span> ${githubHandle(member)}`;
  /** @param {Submission} submission */
  const statusLabel = (submission) =>
    submission.approved ? 'Approved' : submission.ready ? 'Awaiting approval' : 'Review required';
  /** @param {Submission} submission */
  const statusClass = (submission) =>
    submission.approved ? 'aligned' : submission.ready ? 'scope' : 'review';
  /** @param {Submission} submission */
  const verdictLabel = (submission) => (submission.approved ? 'Approved verdict' : 'Proposed verdict');
  /** @param {Submission} submission */
  const statusBadge = (submission) =>
    /* HTML */ `<span class="status-badge status-${statusClass(submission)}"
      ><i aria-hidden="true"></i>${statusLabel(submission)}</span
    >`;
  /**
   * @param {Submission} submission
   * @param {RubricRow} row
   */
  const rowSubject = (submission, row) => {
    if (row.scope === 'shared')
      return submission.members.length > 1 ? 'Shared · all members' : 'Submission';
    const member = submission.members.find((member) => member.id === row.member);
    return member ? memberIdentity(member) : '';
  };
  /** @param {readonly string[]} references */
  const renderEvidence = (references) =>
    /* HTML */ `<div class="evidence-block">
      <div class="panel-label">Evidence</div>
      ${references.map((reference) => /* HTML */ `<code>${escapeHtml(reference)}</code>`).join('')}
    </div>`;
  /** @param {string} text */
  const renderFeedback = (text) => /* HTML */ `<p class="feedback-text">${escapeHtml(text)}</p>`;
  /**
   * @param {Submission} submission
   * @param {Member} member
   */
  const rowsForMember = (submission, member) =>
    submission.rows.filter((row) => row.scope === 'shared' || row.member === member.id);
  /** @param {Submission} submission */
  const studentCount = (submission) =>
    `${submission.members.length} ${submission.members.length === 1 ? 'student' : 'students'}`;
  /** @param {GradeTotals | null} totals */
  const rawTotal = (totals) =>
    totals
      ? `${formatScore(totals.raw)}<small>/${maximum}</small>`
      : '<span class="unavailable">Missing</span>';
  /** @param {GradeTotals | null} totals */
  const weighted = (totals) => (totals ? `${formatScore(totals.weighted)}% weighted` : 'Not available');

  /** @param {readonly Submission[]} visibleSubmissions */
  function renderQueue(visibleSubmissions) {
    const options = [
      ['all', 'All submissions'],
      ['pending', 'Awaiting approval'],
      ['approved', 'Approved'],
      ['attention', 'Review required'],
      ['disagreement', 'At review threshold'],
      ['missing', 'Missing grader'],
      ['group', 'Groups'],
      ['individual', 'Individuals'],
    ];
    return /* HTML */ `<aside class="sidebar" aria-label="Submission queue">
      <div class="sidebar-head">
        <strong>Submissions</strong
        ><span class="mono muted">${visibleSubmissions.length}/${submissions.length}</span>
      </div>
      <div class="queue-controls">
        <label class="search-wrap"
          ><span class="search-icon" aria-hidden="true">⌕</span
          ><input
            type="search"
            data-search
            aria-label="Search submissions"
            placeholder="Name, @handle, ID"
            value="${escapeHtml(state.query)}"
        /></label>
        <div class="queue-tools">
          <select data-filter aria-label="Filter submissions">
            ${options.map(([id, label]) => /* HTML */ `<option value="${id}" ${state.filter === id ? 'selected' : ''}>${label}</option>`).join('')}
          </select>
        </div>
      </div>
      <nav class="queue-list" data-scroll="queue" aria-label="Choose submission">
        ${
          visibleSubmissions
            .map(
              (submission) =>
                /* HTML */ `<button
                  class="queue-item ${submission.id === state.selectedSubmissionId ? 'is-active' : ''}"
                  data-submission="${escapeHtml(submission.id)}"
                  aria-pressed="${submission.id === state.selectedSubmissionId}"
                >
                  <span class="queue-top"
                    ><span class="mono">${escapeHtml(submission.id)}</span
                    ><span class="queue-state status-${statusClass(submission)}"
                      ><i aria-hidden="true"></i>${submission.approved ? 'Approved' : 'Pending'}</span
                    ></span
                  ><strong class="queue-name"
                    >${submission.members.length > 1 ? `${studentCount(submission)} · ${escapeHtml(submission.members.map((member) => member.name).join(', '))}` : escapeHtml(submission.members[0].name)}</strong
                  ><span class="queue-meta"
                    ><span
                      >${submission.members.length > 1 ? studentCount(submission) : githubHandle(submission.members[0])}</span
                    ><span class="mono">Δ ${formatScore(submission.spread)}</span></span
                  >
                </button>`,
            )
            .join('') || '<p class="empty-state">No matching submissions.</p>'
        }
      </nav>
      <div class="sidebar-footer">
        ${submissions.length} submissions ·
        ${submissions.reduce((total, submission) => total + submission.members.length, 0)} students
      </div>
    </aside>`;
  }

  /** @param {Submission} submission */
  function renderMembers(submission) {
    return /* HTML */ `<section class="attribution" aria-label="Submission members and scores">
      <div class="member-band-head">
        <span class="panel-label"
          >Member attribution <span class="muted">· raw totals / ${maximum} + weighted %</span></span
        ><button
          class="all-members ${state.selectedMemberId === null ? 'is-active' : ''}"
          data-all-members
          aria-pressed="${state.selectedMemberId === null}"
        >
          All members
        </button>
      </div>
      <div class="member-band" style="--member-count:${submission.members.length}">
        ${submission.members
          .map(
            (member) =>
              /* HTML */ `<button
                class="member-card ${state.selectedMemberId === member.id ? 'is-active' : ''}"
                data-member="${escapeHtml(member.id)}"
                aria-pressed="${state.selectedMemberId === member.id}"
              >
                <span class="member-identity">${memberIdentity(member)}</span
                ><span class="member-proposed"
                  ><span>${submission.approved ? 'Approved' : 'Proposed'}</span
                  ><strong>${member.proposed ? rawTotal(member.proposed) : 'Pending'}</strong
                  ><small
                    >${member.proposed ? weighted(member.proposed) : 'Awaiting arbitration'}</small
                  ></span
                ><span class="member-totals"
                  >${graders
                    .map((grader) => {
                      const totals = member.totals[grader.id];
                      return /* HTML */ `<span class="score-cell" title="${escapeHtml(grader.name)}">
                        <span>${escapeHtml(grader.code)}</span>
                        <b>${totals ? formatScore(totals.raw) : '—'}</b>
                        <small>${totals ? formatScore(totals.weighted) + '%' : 'Missing'}</small>
                      </span>`;
                    })
                    .join('')}</span
                ><span class="member-scope"
                  >${rowsForMember(submission, member)
                    .filter((row) => row.scope === 'shared')
                    .reduce((total, row) => total + row.max, 0)}
                  shared +
                  ${rowsForMember(submission, member)
                    .filter((row) => row.scope === 'individual')
                    .reduce((total, row) => total + row.max, 0)}
                  individual</span
                >
              </button>`,
          )
          .join('')}
      </div>
    </section>`;
  }

  /**
   * @param {Submission} submission
   * @param {readonly RubricRow[]} rows
   * @param {string} label
   * @param {string} subtitle
   * @param {string} code
   * @param {RubricRow | undefined} selected
   */
  function renderRubricSheet(submission, rows, label, subtitle, code, selected) {
    if (!rows.length) return '';
    return /* HTML */ `<section class="sheet" aria-label="${escapeHtml(label)}">
      <header class="sheet-heading">
        <span class="scope-code">${code}</span>
        <div class="sheet-label">
          <h2>${label}</h2>
          <span>${subtitle}</span>
        </div>
        <span class="mono muted">${rows.length}</span>
      </header>
      <div class="table-scroll">
        <table
          class="score-table"
          style="min-width:${tableBaseWidthPixels + graders.length * graderColumnWidthPixels}px"
        >
          <thead>
            <tr>
              <th scope="col">Criterion / subject</th>
              ${graders.map((grader) => /* HTML */ `<th scope="col" title="${escapeHtml(grader.name)}">${grader.code}</th>`).join('')}
              <th scope="col">Δ</th>
              <th scope="col" class="proposed-column">
                ${submission.approved ? 'Approved' : 'Proposed'}
              </th>
              <th scope="col">Max</th>
            </tr>
          </thead>
          <tbody>
            ${rows
              .map(
                (row) =>
                  /* HTML */ `<tr
                    class="${row.id === selected?.id ? 'is-selected' : ''} ${row.disputed ? 'is-disputed' : ''}"
                  >
                    <th scope="row">
                      <button
                        class="criterion-button"
                        data-row="${row.id}"
                        aria-pressed="${row.id === selected?.id}"
                      >
                        ${escapeHtml(row.name)}<span class="criterion-subject"
                          >${rowSubject(submission, row)} · weight
                          ${row.weight}${row.different && row.spread === 0 ? ' · feedback / evidence differs' : ''}${row.disputed ? ' · review threshold' : ''}</span
                        >
                      </button>
                    </th>
                    ${graders
                      .map((grader) => {
                        const assessment = row.candidates[grader.id];
                        const score = assessment
                          ? formatScore(assessment.score)
                          : '<span class="unavailable" title="Missing evaluation">—</span>';
                        return /* HTML */ `<td class="score candidate-score">${score}</td>`;
                      })
                      .join('')}
                    <td class="delta ${row.disputed ? 'warning' : ''}">${formatScore(row.spread)}</td>
                    <td class="proposed-score">
                      ${row.proposal ? formatScore(row.proposal.score) : 'Pending'}
                    </td>
                    <td class="score muted">${row.max}</td>
                  </tr>`,
              )
              .join('')}
          </tbody>
        </table>
      </div>
    </section>`;
  }

  /** @param {Submission} submission */
  function renderOverallRemark(submission) {
    return /* HTML */ `<section class="overall-note">
      <div class="panel-label">
        Overall remark <span class="muted">· ${verdictLabel(submission).toLowerCase()}</span>
      </div>
      <p class="overall-text feedback-text">
        ${escapeHtml(submission.summary ?? 'Awaiting the proposed overall remark.')}
      </p>
    </section>`;
  }

  /**
   * @param {Submission} submission
   * @param {RubricRow | undefined} row
   */
  function renderInspector(submission, row) {
    if (!row)
      return '<aside class="inspector"><p class="empty-state">No differing criteria in this selection. Turn off “Differences only” to inspect the full rubric.</p></aside>';
    return /* HTML */ `<aside
      class="inspector"
      aria-label="Selected criterion feedback"
      data-scroll="inspector"
    >
      <header class="inspector-header">
        <div class="panel-label">
          Component feedback
          <span class="mono"
            >${graders.filter((grader) => row.candidates[grader.id]).length}/${graders.length}
            candidates</span
          >
        </div>
        <h2>${escapeHtml(row.name)}</h2>
        <div class="inspector-subject">${rowSubject(submission, row)}</div>
      </header>
      <div class="inspector-content">
        <section class="verdict-block">
          <div class="candidate-top">
            <h3>${verdictLabel(submission)}</h3>
            <strong
              >${row.proposal ? formatScore(row.proposal.score) + '<small>/' + row.max + '</small>' : 'Pending'}</strong
            >
          </div>
          ${renderFeedback(row.proposal?.feedback ?? 'Awaiting arbitration.')}${
            row.proposal
              ? renderEvidence(row.proposal.evidence) +
                /* HTML */ `<details class="rationale">
                  <summary>Arbitration rationale</summary>
                  ${renderFeedback(row.proposal.rationale)}
                </details>`
              : ''
          }
        </section>
        ${graders
          .map((grader) => {
            const assessment = row.candidates[grader.id];
            return /* HTML */ `<section class="candidate-block">
              <div class="candidate-top">
                <h3><span class="grader-code">${grader.code}</span>${escapeHtml(grader.name)}</h3>
                <strong
                  >${assessment ? formatScore(assessment.score) + '<small>/' + row.max + '</small>' : 'Missing'}</strong
                >
              </div>
              ${renderFeedback(assessment?.feedback ?? 'Missing evaluation.')}${assessment ? renderEvidence(assessment.evidence) : ''}
            </section>`;
          })
          .join('')}
        <p class="inspector-hint">Select another criterion to compare its feedback and evidence.</p>
      </div>
    </aside>`;
  }

  /** @param {Submission} submission */
  function renderRemarks(submission) {
    return /* HTML */ `<section class="remarks-panel" data-scroll="remarks" aria-label="Overall remarks">
      ${renderOverallRemark(submission)}
      <div class="section-line">
        <h2>Candidate overall remarks</h2>
        <span class="muted">Independent judgements</span>
      </div>
      <div class="remarks-grid">
        ${graders
          .map(
            (grader) =>
              /* HTML */ `<article class="remark-card">
                <header class="candidate-top">
                  <h3><span class="grader-code">${grader.code}</span>${escapeHtml(grader.name)}</h3>
                  <span class="mono muted"
                    >${submission.overall[grader.id] === null ? 'Missing' : 'Complete'}</span
                  >
                </header>
                ${renderFeedback(submission.overall[grader.id] ?? 'Missing evaluation.')}
                <div class="remark-totals">
                  ${submission.members
                    .map(
                      (member) =>
                        /* HTML */ `<span
                          >${memberIdentity(member)}<b class="mono"
                            >${rawTotal(member.totals[grader.id])}<small
                              >${weighted(member.totals[grader.id])}</small
                            ></b
                          ></span
                        >`,
                    )
                    .join('')}
                </div>
              </article>`,
          )
          .join('')}
      </div>
    </section>`;
  }

  /** @param {Submission} submission */
  function reviewFindings(submission) {
    if (!submission.findings) {
      return submission.flags
        .map((flag) => /* HTML */ `<p><span class="flag-marker">!</span>${escapeHtml(flag)}</p>`)
        .join('');
    }
    const labels = { blocker: 'Blocker', grade_impact: 'Grade impact', advisory: 'Advisory' };
    const groupedFindings = submission.findings
      .map(
        (finding) =>
          /* HTML */ ` <details class="finding-row">
            <summary>
              <span class="finding-meta"
                ><span class="finding-${escapeHtml(finding.severity)}"
                  >${escapeHtml(labels[finding.severity])}</span
                >
                · ${escapeHtml(finding.topic)}</span
              >
              <strong>${escapeHtml(finding.title)}</strong>
              ${finding.impact === null ? '' : /* HTML */ `<span class="finding-meta">${escapeHtml(Number(finding.impact.toFixed(impactDecimalPlaces)))} percentage points</span>`}
              ${finding.status === 'reconciled' ? '<span class="finding-meta">Reconciled</span>' : ''}
            </summary>
            <p>${escapeHtml(finding.summary)}</p>
            ${
              finding.sources.length
                ? /* HTML */ `<ul>
                    ${finding.sources
                      .map(
                        (source) =>
                          /* HTML */ `<li>
                            <b>${escapeHtml(source.author)}</b>
                            <span>${escapeHtml(source.observation)}</span
                            ><code>${escapeHtml(source.reference)}</code>
                          </li>`,
                      )
                      .join('')}
                  </ul>`
                : ''
            }
          </details>`,
      )
      .join('');
    const originalFlags = submission.flags.length
      ? /* HTML */ `<details class="finding-row">
          <summary>Original record flags</summary>
          <ul>
            ${submission.flags.map((flag) => /* HTML */ `<li>${escapeHtml(flag)}</li>`).join('')}
          </ul>
        </details>`
      : '';
    return groupedFindings + originalFlags;
  }

  /** @param {Submission} submission */
  function renderReview(submission) {
    const checks = submission.verification?.checks ?? [];
    return /* HTML */ `<section class="review-panel" data-scroll="review" aria-label="TA review">
      <div class="section-line">
        <h2>Review before release</h2>
        ${statusBadge(submission)}
      </div>
      ${stageGuides.length ? /* HTML */ `<nav class="stage-guide-links" aria-label="Staff stage guides">${stageGuides.map((guide) => /* HTML */ `<a href="${escapeHtml(guide.href)}">${escapeHtml(guide.stage)} guide</a>`).join('')}</nav>` : ''}
      <div class="review-flags">
        ${reviewFindings(submission) || '<p>No outstanding review signals.</p>'}
      </div>
      <div class="review-grid">
        <section>
          <h3>Candidate coverage</h3>
          ${graders
            .map((grader) => {
              const missing = submission.missing.includes(grader.id);
              return /* HTML */ `<div class="coverage-row">
                <span class="mono">${escapeHtml(grader.code)}</span>
                <span>${escapeHtml(grader.name)}</span>
                <span class="${missing ? 'danger' : 'success'}"
                  >${missing ? 'Missing' : 'Returned'}</span
                >
              </div>`;
            })
            .join('')}
        </section>
        <section>
          <h3>Pinned source</h3>
          <p class="source-reference">${escapeHtml(submission.source.repo)}</p>
          ${submission.source.branch ? /* HTML */ `<p>Branch <code>${escapeHtml(submission.source.branch)}</code></p>` : ''}
          <p class="source-reference">
            ${submission.source.branch ? 'Graded commit' : 'Source fingerprint'}
            <code>${escapeHtml(submission.source.commit)}</code>
          </p>
          <p>
            Each total includes shared scores plus the named student's individual assessments. Weights
            determine the percentage, independently of the raw total.
          </p>
        </section>
      </div>
      <section class="review-policy">
        <h3>
          Sanity review ·
          ${submission.verification ? (submission.verificationCurrent ? 'Current' : 'Stale') : 'Missing'}
        </h3>
        ${
          checks
            .map(
              (check) =>
                /* HTML */ `<div class="review-check">
                  <span class="${check.status === 'unverified' ? 'warning' : 'muted'}"
                    >${escapeHtml(check.status)}</span
                  >
                  <h3>${escapeHtml(check.claim)}</h3>
                  ${renderFeedback(check.finding)}${renderEvidence(check.evidence)}
                </div>`,
            )
            .join('') || '<p>No sanity review returned.</p>'
        }
      </section>
      <section class="review-policy">
        <h3>Corrections · ${submission.history.length}</h3>
        ${submission.history.map((change) => /* HTML */ `<div class="review-check">${renderFeedback(change.reason)}${renderEvidence(change.evidence)}</div>`).join('') || '<p>No recorded corrections.</p>'}
      </section>
      <section class="review-policy">
        <h3>Human approval</h3>
        <p>
          ${submission.approved ? 'This submission has current human approval.' : submission.ready ? 'Grading and sanity review are complete. Current human approval is still required.' : 'Resolve the missing or stale records above before approval.'}
          This report is read-only. Record approvals through the grading CLI after reviewing the
          evidence; exporting grades is a separate action.
        </p>
        <p>
          Review thresholds: criterion spread ${thresholds.criterion_spread} points; total spread
          ${thresholds.total_spread} points. Candidate disagreement does not automatically determine the
          verdict.
        </p>
      </section>
    </section>`;
  }

  /** @param {Submission} submission */
  function matchesFilters(submission) {
    /** @type {Readonly<Record<string, boolean>>} */
    const filters = {
      all: true,
      pending: !submission.approved,
      approved: submission.approved,
      attention: !submission.ready,
      disagreement: submission.disputed,
      missing: submission.missing.length > 0,
      group: submission.type === 'group',
      individual: submission.type === 'individual',
    };
    return (
      filters[state.filter] &&
      `${submission.id} ${submission.members.map((member) => `${member.id} ${member.name} @${member.github}`).join(' ')}`
        .toLowerCase()
        .includes(state.query.trim().toLowerCase())
    );
  }

  function scrollPanels() {
    return /** @type {NodeListOf<HTMLElement>} */ (reportRoot.querySelectorAll('[data-scroll]'));
  }

  /** @param {string} selector */
  function control(selector) {
    return /** @type {HTMLElement | null} */ (reportRoot.querySelector(selector));
  }

  function render({ preserveScroll = true } = {}) {
    const scroll = preserveScroll
      ? Object.fromEntries(
          [...scrollPanels()].map((element) => [element.dataset.scroll ?? '', element.scrollTop]),
        )
      : {};
    const visibleSubmissions = submissions.filter(matchesFilters);
    if (
      visibleSubmissions.length &&
      !visibleSubmissions.some((submission) => submission.id === state.selectedSubmissionId)
    ) {
      state.selectedSubmissionId = visibleSubmissions[0].id;
      state.selectedMemberId = null;
      state.selectedRowId = null;
    }
    const submission = visibleSubmissions.find(
      (submission) => submission.id === state.selectedSubmissionId,
    );
    let mainContent =
      '<main class="main is-empty"><div class="report-body"><section class="empty-state report-empty"><h2>No matching submissions</h2><p>Change the search or filter to show a report.</p></section></div></main>';
    if (submission) {
      const chosen = submission.members.find((member) => member.id === state.selectedMemberId);
      const rows = submission.rows.filter(
        (row) =>
          (!chosen || row.scope === 'shared' || row.member === chosen.id) &&
          (!state.differencesOnly || row.different),
      );
      const row = rows.find((row) => row.id === state.selectedRowId) ?? rows[0];
      state.selectedRowId = row?.id ?? null;
      const body =
        state.view === 'remarks'
          ? renderRemarks(submission)
          : state.view === 'review'
            ? renderReview(submission)
            : /* HTML */ `<section class="ledger-panel" data-scroll="ledger" aria-label="Rubric ledger">
                  ${renderRubricSheet(
                    submission,
                    rows.filter((row) => row.scope === 'shared'),
                    submission.members.length > 1 ? 'Shared work' : 'Submission work',
                    'Applies to every submission member',
                    'S',
                    row,
                  )}${renderRubricSheet(
                    submission,
                    rows.filter((row) => row.scope === 'individual'),
                    'Individual work',
                    chosen ? escapeHtml(chosen.name) : 'Each assessment belongs to its named student',
                    'I',
                    row,
                  )}${rows.length ? '' : '<p class="empty-state">No differing criteria in this selection.</p>'}${renderOverallRemark(submission)}
                  <p class="totals-note">
                    ${graders.map((grader) => `${grader.code} / ${escapeHtml(grader.name)}`).join(' · ')}.
                    Δ compares available raw scores. Missing reviews remain missing, never zero. Verdicts
                    are separate judgements, not averages.
                  </p>
                </section>
                ${renderInspector(submission, row)}`;
      mainContent = /* HTML */ `<main class="main">
        <header class="submission-head">
          <div class="submission-title">
            <div class="submission-meta">
              <span class="mono">${escapeHtml(submission.id)}</span
              ><span
                >${submission.type === 'group' ? studentCount(submission) + ' · group' : 'Individual'}</span
              ><span class="mono" title="${escapeHtml(submission.source.commit)}"
                >${escapeHtml(submission.source.commit.slice(0, sourceDigestPrefixLength))}</span
              >
            </div>
            <h1>${escapeHtml(title)}</h1>
            <div class="identity-line">
              ${submission.members.map(memberIdentity).join('<span class="identity-separator">/</span>')}
            </div>
          </div>
          <div class="submission-state">
            ${statusBadge(submission)}<span class="spread-summary"
              ><span class="muted">Candidate spread</span
              ><b
                class="mono ${submission.spread !== null && submission.spread >= thresholds.total_spread ? 'warning' : ''}"
                >${submission.spread === null ? 'Insufficient reviews' : formatScore(submission.spread) + ' pts'}</b
              ></span
            >
          </div>
        </header>
        ${renderMembers(submission)}
        <div class="report-toolbar">
          <nav class="view-tabs" aria-label="Report views">
            ${[
              ['scores', 'Scores & feedback'],
              ['remarks', 'Overall remarks'],
              [
                'review',
                'TA review' +
                  ((submission.findings ?? submission.flags).length
                    ? ' · ' + (submission.findings ?? submission.flags).length
                    : ''),
              ],
            ]
              .map(
                ([id, label]) =>
                  /* HTML */ `<button
                    data-view="${id}"
                    class="${state.view === id ? 'is-active' : ''}"
                    aria-pressed="${state.view === id}"
                  >
                    ${label}
                  </button>`,
              )
              .join('')}
          </nav>
          <span class="focus-label">${chosen ? escapeHtml(chosen.name) : 'All members'}</span
          ><label class="only-differences"
            ><input
              type="checkbox"
              data-differences
              ${state.differencesOnly ? 'checked' : ''}
              ${state.view !== 'scores' ? 'disabled' : ''}
            />Differences only</label
          >
        </div>
        <div class="report-body view-${state.view}">${body}</div>
      </main>`;
    }
    reportRoot.innerHTML = /* HTML */ `<div class="app-shell">
      <header class="app-bar">
        <span class="wordmark"><span class="brand-symbol" aria-hidden="true">m</span>Markwire</span
        ><span class="design-label">Columns</span
        ><span class="review-label">Internal TA review · read-only</span>
      </header>
      <div class="workspace">${renderQueue(visibleSubmissions)}${mainContent}</div>
      <footer class="status-bar">
        <span>Grading and verification</span
        ><span
          >${submissions.filter((submission) => submission.approved).length}/${submissions.length}
          submissions approved · No grades released by this report</span
        >
      </footer>
    </div>`;
    for (const panel of scrollPanels()) {
      const position = scroll[panel.dataset.scroll ?? ''];
      if (position !== undefined) panel.scrollTop = position;
    }
    announcer.textContent = `${visibleSubmissions.length} matching submissions. ${submission ? submission.id + ', ' + submission.members.map((member) => member.name).join(', ') : 'No report selected'}.`;
  }
  reportRoot.addEventListener('input', (event) => {
    const search = /** @type {HTMLInputElement | null} */ (event.target);
    if (!search?.matches?.('[data-search]')) return;
    const cursorPosition = search.selectionStart;
    state.query = search.value;
    render();
    const searchControl = /** @type {HTMLInputElement | null} */ (control('[data-search]'));
    searchControl?.focus();
    if (cursorPosition !== null) searchControl?.setSelectionRange(cursorPosition, cursorPosition);
  });
  reportRoot.addEventListener('change', (event) => {
    const selection = /** @type {HTMLSelectElement | null} */ (event.target);
    if (selection?.matches?.('[data-filter]')) {
      state.filter = selection.value;
      render();
      control('[data-filter]')?.focus();
      return;
    }
    const differences = /** @type {HTMLInputElement | null} */ (event.target);
    if (differences?.matches('[data-differences]')) {
      state.differencesOnly = differences.checked;
      render();
      control('[data-differences]')?.focus();
    }
  });
  reportRoot.addEventListener('click', (event) => {
    const target = /** @type {Element | null} */ (event.target);
    const button = target?.closest?.('button');
    if (!button) return;
    let selector;
    if (button.dataset.submission) {
      state.selectedSubmissionId = button.dataset.submission;
      state.selectedMemberId = null;
      state.selectedRowId = null;
      selector = `[data-submission="${state.selectedSubmissionId}"]`;
      const url = new URL(location.href);
      url.searchParams.set('submission', button.dataset.submission);
      history.replaceState({}, '', url);
    } else if ('allMembers' in button.dataset) {
      state.selectedMemberId = null;
      state.selectedRowId = null;
      selector = '[data-all-members]';
    } else if (button.dataset.member) {
      state.selectedMemberId = button.dataset.member;
      state.selectedRowId = null;
      selector = `[data-member="${state.selectedMemberId}"]`;
    } else if (button.dataset.row) {
      state.selectedRowId = button.dataset.row;
      selector = `[data-row="${state.selectedRowId}"]`;
    } else if (button.dataset.view) {
      state.view = button.dataset.view;
      selector = `[data-view="${state.view}"]`;
    } else return;
    render({ preserveScroll: Boolean(button.dataset.row) });
    if (button.dataset.row) reportRoot.querySelector('[data-scroll="inspector"]')?.scrollTo(0, 0);
    control(selector)?.focus({ preventScroll: true });
  });
  render();
})();
