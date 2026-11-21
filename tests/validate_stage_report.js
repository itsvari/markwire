'use strict';

// Exercises the stage report decision hand-off without a browser:
// node tests/validate_stage_report.js <workspace>/reports/grading.html

const fileSystem = require('node:fs');
const virtualMachine = require('node:vm');
const assert = require('node:assert/strict');

const reportPath = process.argv[2];
assert.ok(reportPath, 'Pass the path of a generated stage report.');

const reportPage = fileSystem.readFileSync(reportPath, 'utf8');
const dataElement = reportPage.match(/<script id="stage-data" type="application\/json">([\s\S]*?)<\/script>/);
const scriptElement = reportPage.match(/<script>([\s\S]*?)<\/script>/);
assert.ok(dataElement && scriptElement, 'The stage report must embed its data and renderer.');
const reportData = JSON.parse(dataElement[1]);
const reportScript = scriptElement[1];

const HTML_ENTITIES = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'" };
const unescapeHtml = value => value.replace(/&(amp|lt|gt|quot|#39);/g, entity => HTML_ENTITIES[entity]);
const isActionable = finding => finding.status === 'open' && finding.options.length > 0;

function loadReport(payload) {
  const listeners = new Map();
  const clipboard = [];
  const downloads = [];
  let markup = '';
  const stub = () => ({
    dataset: {},
    scrollTop: 0,
    focus() {},
    select() {},
    setSelectionRange() {},
    scrollIntoView() {},
  });
  const application = {
    get innerHTML() {
      return markup;
    },
    set innerHTML(content) {
      markup = content;
    },
    addEventListener(eventType, listener) {
      listeners.set(eventType, listener);
    },
    after() {},
    querySelectorAll: () => [],
    querySelector: stub,
  };
  const announcer = { textContent: '', className: '', setAttribute() {} };
  const context = {
    Blob,
    Intl,
    setTimeout,
    URL: {
      createObjectURL(blob) {
        downloads.push(blob);
        return `blob:${downloads.length}`;
      },
      revokeObjectURL() {},
    },
    CSS: { escape: value => String(value).replace(/["\\]/g, '\\$&') },
    navigator: {
      clipboard: {
        async writeText(text) {
          clipboard.push(text);
        },
      },
    },
    document: {
      getElementById(identifier) {
        return {
          'stage-app': application,
          'stage-data': { textContent: JSON.stringify(payload) },
        }[identifier];
      },
      createElement: tag => (tag === 'a' ? { click() {} } : announcer),
    },
  };
  virtualMachine.runInNewContext(reportScript, context);

  const disabled = attribute => {
    const button = markup.match(new RegExp(`<button[^>]*\\b${attribute}\\b[^>]*>`));
    assert.ok(button, `Missing ${attribute} button`);
    return /\sdisabled(\s|>|$)/.test(button[0]);
  };
  return {
    get markup() {
      return markup;
    },
    announcer,
    clipboard,
    downloads,
    disabled,
    click(dataset, attribute) {
      return listeners.get('click')({
        target: { closest: () => ({ dataset, disabled: attribute ? disabled(attribute) : false }) },
      });
    },
    select(findingId) {
      return this.click({ finding: findingId });
    },
    choose(optionId) {
      listeners.get('change')({ target: { matches: selector => selector === '[data-option]', value: optionId } });
    },
    note(value) {
      listeners.get('input')({
        target: { matches: selector => selector === '[data-note]', value, selectionStart: value.length, scrollTop: 0 },
      });
    },
    search(value) {
      listeners.get('input')({
        target: { matches: selector => selector === '[data-search]', value, selectionStart: value.length },
      });
    },
    filter(value) {
      listeners.get('change')({ target: { matches: selector => selector === '[data-filter]', value } });
    },
    selectedId() {
      return markup.match(/data-finding="([^"]+)"\s+aria-pressed="true"/)?.[1];
    },
    packetText() {
      const output = markup.match(/<textarea[^>]*data-json[^>]*>\n?([\s\S]*?)<\/textarea/);
      assert.ok(output, 'Missing decision JSON output');
      return unescapeHtml(output[1]);
    },
  };
}

function exportLocked(report) {
  assert.ok(report.disabled('data-copy'), 'Copy must stay disabled until every finding is answered');
  assert.ok(report.disabled('data-download'), 'Download must stay disabled until every finding is answered');
  assert.equal(report.packetText(), '');
}

async function checksSingleCompletePacket(payload) {
  const actionable = payload.findings.filter(isActionable);
  assert.ok(actionable.length >= 2, 'The fixture needs at least two actionable findings.');
  const report = loadReport(payload);
  assert.equal((report.markup.match(/data-copy/g) ?? []).length, 1, 'Exactly one copy control');
  assert.equal((report.markup.match(/data-download/g) ?? []).length, 1, 'Exactly one download control');
  assert.ok(report.markup.includes('Decision hand-off'));
  exportLocked(report);

  // A single answer, or every answer inside a filtered view, must not unlock a partial packet.
  const [first, ...rest] = actionable;
  await report.select(first.id);
  report.choose(first.options[0].id);
  exportLocked(report);
  assert.ok(report.markup.includes(`<b>1</b>/${actionable.length}`));
  await report.click({ copy: '' });
  await report.click({ download: '' });
  assert.equal(report.clipboard.length + report.downloads.length, 0, 'No partial packet may leave the page');

  report.search(first.title.slice(0, 12));
  exportLocked(report);

  // "Next unanswered" reveals remaining findings hidden by search or filters.
  report.filter('answered');
  await report.click({ next: '' }, 'data-next');
  assert.notEqual(report.selectedId(), first.id);
  assert.ok(rest.some(finding => finding.id === report.selectedId()));
  assert.ok(report.announcer.textContent.includes('Filters cleared'));

  // Custom answers need instructions before they count.
  const custom = rest.find(finding => finding.options.some(option => option.id === 'custom')) ?? rest[0];
  await report.select(custom.id);
  report.choose('custom');
  assert.ok(report.markup.includes('Needs note'));
  report.choose('not-an-option');
  assert.ok(report.markup.includes('Needs note'), 'Unknown options are ignored');

  const remaining = rest.filter(finding => finding.id !== custom.id);
  for (const finding of remaining) {
    await report.select(finding.id);
    const defer = finding.options.find(option => option.id === 'defer');
    report.choose((defer ?? finding.options[0]).id);
    report.note(defer ? '' : '  context for the next agent  ');
  }
  exportLocked(report);
  await report.select(custom.id);
  report.note('  Re-run the profile check.  ');

  assert.ok(!report.disabled('data-copy') && !report.disabled('data-download'));
  assert.ok(report.markup.includes('Ready to hand off'));
  assert.ok(report.announcer.textContent.includes('ready'));
  await report.click({ preview: '' }, 'data-preview');
  const shown = report.packetText();
  await report.click({ copy: '' }, 'data-copy');
  await report.click({ download: '' }, 'data-download');
  const downloaded = await report.downloads[0].text();
  assert.equal(report.clipboard[0], downloaded, 'Copy and download must produce identical JSON');
  assert.equal(shown, downloaded, 'The preview must match the exported JSON');

  const packet = JSON.parse(downloaded);
  assert.deepEqual(Object.keys(packet), ['schemaVersion', 'runId', 'stage', 'reviewDigest', 'decisions']);
  assert.equal(packet.schemaVersion, payload.schema_version);
  assert.equal(packet.runId, payload.run_id);
  assert.equal(packet.stage, payload.stage);
  assert.equal(packet.reviewDigest, payload.review_digest);
  assert.deepEqual(
    packet.decisions.map(decision => decision.issueId),
    actionable.map(finding => finding.id).sort(),
    'The packet covers every actionable finding exactly once, sorted by id',
  );
  for (const decision of packet.decisions) {
    const finding = actionable.find(item => item.id === decision.issueId);
    assert.ok(finding.options.some(option => option.id === decision.optionId));
    assert.ok(Object.keys(decision).every(key => ['issueId', 'optionId', 'note'].includes(key)));
    if ('note' in decision) assert.equal(decision.note, decision.note.trim());
    if ('note' in decision) assert.ok(decision.note);
  }
  assert.equal(packet.decisions.find(decision => decision.issueId === custom.id).note, 'Re-run the profile check.');
  assert.ok(packet.decisions.some(decision => decision.optionId === 'defer') || !remaining.length);

  await report.select(first.id);
  await report.click({ clear: '' }, 'data-clear');
  exportLocked(report);
}

function checksNonActionableFindings(payload) {
  const informational = structuredClone(payload);
  for (const finding of informational.findings) {
    finding.status = 'reconciled';
    finding.options = [];
  }
  informational.findings[0].title = '<img src=x onerror=bad()>';
  const report = loadReport(informational);
  exportLocked(report);
  assert.ok(report.markup.includes('No decisions are needed'));
  assert.ok(report.markup.includes('&lt;img src=x onerror=bad()&gt;'));
  assert.ok(!report.markup.includes('<img src=x'));

  const empty = loadReport({ ...payload, findings: [] });
  exportLocked(empty);
  assert.ok(empty.markup.includes('guide'));
}

(async () => {
  await checksSingleCompletePacket(reportData);
  checksNonActionableFindings(reportData);
  console.log(`PASS: ${reportData.findings.filter(isActionable).length} actionable findings, one complete packet.`);
})().catch(error => {
  console.error(error);
  process.exit(1);
});
