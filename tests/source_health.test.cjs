const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');

// Load only the source-health helpers from web/app.js (the rest of the file needs a DOM).
function loadHealth() {
  const code = readFileSync(join(__dirname, '../web/app.js'), 'utf8');
  const start = code.indexOf('// Health summary for a source');
  const end = code.indexOf('function sourceRow(src)');
  assert.ok(start > 0 && end > start, 'health helpers not found in app.js');
  const context = vm.createContext({ Date });
  vm.runInContext(code.slice(start, end) + '\nthis.sourceHealth = sourceHealth;', context);
  return context.sourceHealth;
}

const NOW_S = () => Date.now() / 1000;
const base = { enabled: true, hidden: false, error_streak: 0, last_status: 'ok',
  last_error: '', last_refreshed_at: NOW_S() - 60 };

test('a healthy, recently refreshed source has no health entry', () => {
  const sourceHealth = loadHealth();
  assert.equal(sourceHealth({ ...base }), null);
});

test('failing source reports the streak and the last error', () => {
  const sourceHealth = loadHealth();
  const h = sourceHealth({ ...base, last_status: 'error', error_streak: 3, last_error: 'HTTP 500' });
  assert.equal(h.level, 'fail');
  assert.match(h.text, /3 failed attempts/);
  assert.match(h.text, /HTTP 500/);
});

test('a single failure is singular', () => {
  const sourceHealth = loadHealth();
  const h = sourceHealth({ ...base, last_status: 'error', error_streak: 1 });
  assert.match(h.text, /1 failed attempt\b/);
});

test('error status without a streak still counts as failing', () => {
  const sourceHealth = loadHealth();
  const h = sourceHealth({ ...base, last_status: 'error', error_streak: 0 });
  assert.equal(h.level, 'fail');
});

test('enabled source not refreshed for 3+ days is stale', () => {
  const sourceHealth = loadHealth();
  const h = sourceHealth({ ...base, last_refreshed_at: NOW_S() - 4 * 24 * 3600 });
  assert.equal(h.level, 'stale');
});

test('disabled or hidden sources are not flagged as stale', () => {
  const sourceHealth = loadHealth();
  const old = NOW_S() - 10 * 24 * 3600;
  assert.equal(sourceHealth({ ...base, enabled: false, last_refreshed_at: old }), null);
  assert.equal(sourceHealth({ ...base, hidden: true, last_refreshed_at: old }), null);
});

test('a source that has never refreshed is not flagged as stale', () => {
  const sourceHealth = loadHealth();
  assert.equal(sourceHealth({ ...base, last_refreshed_at: 0 }), null);
});

// Status label shown in the Sources manager list.
function loadStatus() {
  const code = readFileSync(join(__dirname, '../web/app.js'), 'utf8');
  const start = code.indexOf('function sourceStatus(src)');
  const end = code.indexOf('async function patchSource(');
  assert.ok(start > 0 && end > start, 'sourceStatus not found in app.js');
  const context = vm.createContext({ Date, timeago: () => '5m ago' });
  vm.runInContext(code.slice(start, end) + '\nthis.sourceStatus = sourceStatus;', context);
  return context.sourceStatus;
}

test('manager status: paused, failing, never refreshed, and ok', () => {
  const sourceStatus = loadStatus();
  assert.deepEqual({ ...sourceStatus({ ...base, enabled: false }) }, { text: 'Paused', cls: '' });
  const failing = sourceStatus({ ...base, last_status: 'error', error_streak: 2, last_error: 'boom' });
  assert.equal(failing.text, 'Failing (2)');
  assert.equal(failing.cls, 'fail');
  assert.equal(failing.title, 'boom');
  assert.equal(sourceStatus({ ...base, last_refreshed_at: 0 }).text, 'Never refreshed');
  assert.equal(sourceStatus({ ...base }).text, 'OK · 5m ago');
});
