const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');

// Load only the api() helper from web/app.js (the rest of the file needs a DOM).
function loadApi() {
  const code = readFileSync(join(__dirname, '../web/app.js'), 'utf8');
  const start = code.indexOf('async function api(path, opts)');
  const end = code.indexOf('function el(tag, attrs = {}, ...children)');
  assert.ok(start > 0 && end > start, 'api() helper not found in app.js');
  const calls = [];
  const context = vm.createContext({
    fetch: async (url, options) => {
      calls.push({ url, options });
      return { ok: true, json: async () => ({ ok: true }) };
    },
  });
  vm.runInContext(code.slice(start, end) + '\nthis.api = api;', context);
  return { api: context.api, calls };
}

test('a read is left exactly as it was asked for', async () => {
  const { api, calls } = loadApi();
  await api('/items?limit=50');
  assert.equal(calls[0].url, '/api/items?limit=50');
  assert.equal(calls[0].options.method, undefined);
  assert.equal(calls[0].options.headers, undefined);
  assert.equal(calls[0].options.body, undefined);
});

test('a write without a content type gets a JSON one', async () => {
  const { api, calls } = loadApi();
  await api('/items/7/read', { method: 'POST' });
  assert.equal(calls[0].options.headers['Content-Type'], 'application/json');
  assert.equal(calls[0].options.body, '{}');
});

test('a write that already names a content type keeps it', async () => {
  const { api, calls } = loadApi();
  await api('/sources/import.opml', {
    method: 'POST', body: '<opml/>', headers: { 'Content-Type': 'application/xml' },
  });
  assert.equal(calls[0].options.headers['Content-Type'], 'application/xml');
  assert.equal(calls[0].options.body, '<opml/>');
});

test('an existing content type is not overwritten, whatever its case', async () => {
  const { api, calls } = loadApi();
  await api('/items', { method: 'POST', body: 'x', headers: { 'content-type': 'text/xml' } });
  assert.equal(calls[0].options.headers['content-type'], 'text/xml');
  assert.equal(calls[0].options.headers['Content-Type'], undefined);
  assert.equal(calls[0].options.body, 'x');
});

test('other headers are kept', async () => {
  const { api, calls } = loadApi();
  await api('/items', { method: 'POST', headers: { 'X-Test': '1' } });
  assert.equal(calls[0].options.headers['X-Test'], '1');
  assert.equal(calls[0].options.headers['Content-Type'], 'application/json');
});

test('a delete is not touched: it already asks the server first', async () => {
  const { api, calls } = loadApi();
  await api('/items/7', { method: 'DELETE' });
  assert.equal(calls[0].options.method, 'DELETE');
  assert.equal(calls[0].options.headers, undefined);
  assert.equal(calls[0].options.body, undefined);
});

test('the caller\'s options object is not modified', async () => {
  const { api, calls } = loadApi();
  const opts = { method: 'POST' };
  await api('/items/7/read', opts);
  assert.deepEqual(opts, { method: 'POST' });
  assert.equal(calls[0].options.headers['Content-Type'], 'application/json');
});

test('a failed request reports the server message', async () => {
  const code = readFileSync(join(__dirname, '../web/app.js'), 'utf8');
  const start = code.indexOf('async function api(path, opts)');
  const end = code.indexOf('function el(tag, attrs = {}, ...children)');
  const context = vm.createContext({
    fetch: async () => ({ ok: false, status: 403, json: async () => ({ detail: 'origin not allowed' }) }),
  });
  vm.runInContext(code.slice(start, end) + '\nthis.api = api;', context);
  await assert.rejects(() => context.api('/items', { method: 'POST' }),
    /origin not allowed/);
});
