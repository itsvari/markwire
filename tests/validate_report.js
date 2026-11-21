'use strict';

const fileSystem = require('node:fs');
const virtualMachine = require('node:vm');
const assert = require('node:assert/strict');

const reportPath = process.argv[2];
assert.ok(reportPath, 'Pass the path of a generated Columns report.');

const reportPage = fileSystem.readFileSync(reportPath, 'utf8');
const dataElement = reportPage.match(/<script id="report-data" type="application\/json">([\s\S]*?)<\/script>/);
const scriptElement = reportPage.match(/<script>([\s\S]*?)<\/script>/);
assert.ok(dataElement && scriptElement, 'The report must embed its data and renderer.');

const reportData = JSON.parse(dataElement[1]);
const reportScript = scriptElement[1];
const SCROLLED_POSITION_PX = 75;
const HTML_ENTITIES = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
};

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, character => HTML_ENTITIES[character]);
}

function loadReport(payload) {
  const listeners = new Map();
  const scrollPositions = new Map();
  let renderedContent = '';
  const application = {
    get innerHTML() {
      return renderedContent;
    },
    set innerHTML(content) {
      renderedContent = content;
      scrollPositions.clear();
      for (const [, panel] of content.matchAll(/data-scroll="([^"]+)"/g)) {
        scrollPositions.set(panel, {
          dataset: { scroll: panel },
          scrollTop: 0,
          scrollTo(_horizontalPosition, verticalPosition) {
            this.scrollTop = verticalPosition;
          },
        });
      }
    },
    addEventListener(eventType, listener) {
      listeners.set(eventType, listener);
    },
    querySelectorAll() {
      return [...scrollPositions.values()];
    },
    querySelector(selector) {
      const panel = selector.match(/data-scroll="([^"]+)"/)?.[1];
      return scrollPositions.get(panel) ?? { focus() {}, setSelectionRange() {} };
    },
  };
  const announcer = { textContent: '' };
  const elements = {
    app: application,
    'report-data': { textContent: JSON.stringify(payload) },
    announcer,
  };
  const context = {
    URL,
    console,
    location: new URL('http://localhost/review.html?submission=absent'),
    history: { replaceState() {} },
    document: {
      getElementById(identifier) {
        return elements[identifier];
      },
    },
  };
  virtualMachine.runInNewContext(reportScript, context);
  return {
    application,
    scrollPositions,
    click(dataset) {
      listeners.get('click')({ target: { closest: () => ({ dataset }) } });
    },
    search(value) {
      listeners.get('input')({
        target: { matches: selector => selector === '[data-search]', value, selectionStart: value.length },
      });
    },
    filter(value) {
      listeners.get('change')({
        target: { matches: selector => selector === '[data-filter]', value },
      });
    },
    differences(checked) {
      listeners.get('change')({
        target: { matches: selector => selector === '[data-differences]', checked },
      });
    },
  };
}

function rowIdentifiers(markup) {
  return [...markup.matchAll(/data-row="([^"]+)"/g)].map(match => match[1]);
}

function showsRows(report, rows) {
  assert.deepEqual(rowIdentifiers(report.application.innerHTML), rows.map(row => row.id));
}

function showsEvidence(report, row) {
  for (const assessment of [...Object.values(row.candidates), row.proposal].filter(Boolean)) {
    assert.ok(report.application.innerHTML.includes(escapeHtml(assessment.feedback)));
    for (const reference of assessment.evidence) {
      assert.ok(report.application.innerHTML.includes(escapeHtml(reference)));
    }
  }
  if (row.proposal) {
    assert.ok(report.application.innerHTML.includes(escapeHtml(row.proposal.rationale)));
  }
}

function showsSubmission(report, submission) {
  report.click({ submission: submission.id });
  report.click({ view: 'scores' });
  showsRows(report, submission.rows);

  for (const member of submission.members) {
    report.click({ member: member.id });
    showsRows(report, submission.rows.filter(row => row.scope === 'shared' || row.member === member.id));
    assert.ok(report.application.innerHTML.includes(escapeHtml(member.name)));
    if (member.github) {
      assert.ok(report.application.innerHTML.includes(`@${member.github}`));
    }
  }

  report.click({ allMembers: '' });
  for (const row of submission.rows) {
    report.scrollPositions.get('ledger').scrollTop = SCROLLED_POSITION_PX;
    report.click({ row: row.id });
    assert.equal(report.scrollPositions.get('ledger').scrollTop, SCROLLED_POSITION_PX);
    assert.equal(report.scrollPositions.get('inspector').scrollTop, 0);
    showsEvidence(report, row);
  }

  report.differences(true);
  showsRows(report, submission.rows.filter(row => row.different));
  report.differences(false);
  report.click({ view: 'remarks' });
  for (const remark of Object.values(submission.overall).filter(Boolean)) {
    assert.ok(report.application.innerHTML.includes(escapeHtml(remark)));
  }

  report.click({ view: 'review' });
  for (const check of submission.verification?.checks ?? []) {
    assert.ok(report.application.innerHTML.includes(escapeHtml(check.finding)));
  }
  for (const correction of submission.history) {
    assert.ok(report.application.innerHTML.includes(escapeHtml(correction.reason)));
  }
  if (!submission.findings) {
    for (const flag of submission.flags) {
      assert.ok(report.application.innerHTML.includes(escapeHtml(flag)));
    }
  }
  return submission.rows.length;
}

function checksFilters(report, submissions) {
  report.search('no-matching-student');
  assert.ok(report.application.innerHTML.includes('No matching submissions'));
  assert.ok(!report.application.innerHTML.includes('class="member-card'));
  report.search('');
  for (const [filter, approved] of [['approved', true], ['pending', false]]) {
    report.filter(filter);
    const renderedCount = (report.application.innerHTML.match(/data-submission=/g) ?? []).length;
    assert.equal(renderedCount, submissions.filter(submission => submission.approved === approved).length);
  }
}

function checksMissingCandidate(data) {
  if (!data.submissions.length) {
    return;
  }
  const partial = structuredClone(data);
  const missingGrader = 'missing_candidate';
  partial.graders.push({ id: missingGrader, name: 'Fourth <candidate>', code: 'G4' });
  for (const submission of partial.submissions) {
    submission.missing.push(missingGrader);
    submission.overall[missingGrader] = null;
    submission.approved = false;
    submission.ready = false;
    for (const row of submission.rows) {
      row.candidates[missingGrader] = null;
      row.proposal = null;
      row.different = true;
    }
    for (const member of submission.members) {
      member.totals[missingGrader] = null;
      member.proposed = null;
    }
  }
  partial.submissions[0].members[0].name = '<img src=x onerror=bad()>';
  const report = loadReport(partial);
  assert.ok(report.application.innerHTML.includes('&lt;img src=x onerror=bad()&gt;'));
  assert.ok(!report.application.innerHTML.includes('<img src=x'));
  assert.ok(report.application.innerHTML.includes('Fourth &lt;candidate&gt;'));
  assert.ok(report.application.innerHTML.includes('Missing evaluation.'));
  assert.ok(report.application.innerHTML.includes('Awaiting arbitration'));
}

function checksNamedMember(data) {
  const identityCase = structuredClone(data);
  const group = identityCase.submissions.find(submission => submission.members.length > 1);
  if (!group) {
    return;
  }
  const previous = group.members[0].id;
  group.members[0].id = 'all';
  for (const row of group.rows) {
    if (row.member === previous) {
      row.member = 'all';
    }
  }
  const report = loadReport(identityCase);
  report.click({ submission: group.id });
  showsRows(report, group.rows);
  report.click({ member: 'all' });
  showsRows(report, group.rows.filter(row => row.scope === 'shared' || row.member === 'all'));
  report.click({ allMembers: '' });
  showsRows(report, group.rows);
}

const report = loadReport(reportData);
const testedRows = reportData.submissions.reduce((count, submission) => count + showsSubmission(report, submission), 0);
checksFilters(report, reportData.submissions);
const emptyReport = loadReport({ ...reportData, submissions: [] });
assert.ok(emptyReport.application.innerHTML.includes('No matching submissions'));
checksMissingCandidate(reportData);
checksNamedMember(reportData);

console.log(`PASS: ${reportData.submissions.length} submissions and ${testedRows} criterion selections.`);
