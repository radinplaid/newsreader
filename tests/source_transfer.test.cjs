const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');

function setup() {
  const nodes = new Map();
  const calls = [];
  const $ = (id) => {
    if (!nodes.has(id)) {
      const classes = new Set();
      nodes.set(id, {
        textContent: '', disabled: false, value: '', handlers: {},
        classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c),
          toggle: (c, on) => on ? classes.add(c) : classes.delete(c), contains: (c) => classes.has(c) },
        addEventListener(event, fn) { this.handlers[event] = fn; },
        setAttribute() {}, focus() { this.focused = true; },
        showModal() { this.open = true; },
        close() { this.open = false; this.handlers.close(); }, click() {},
      });
    }
    return nodes.get(id);
  };
  const context = vm.createContext({ $, Blob, URL, Date,
    api: async (path, options) => { calls.push({ path, options }); return { created: 1, updated: 2, skipped: 3 }; },
    loadSidebar: async () => calls.push('sidebar'), loadItems: async () => calls.push('items'),
    fetch: async () => ({ ok: true, text: async () => '<opml/>' }),
    el: (_tag, attrs) => { calls.push(attrs); return { ...attrs, click() {} }; },
  });
  const code = readFileSync(join(__dirname, '../web/app.js'), 'utf8');
  vm.runInContext(code.slice(code.indexOf('/* ---------------- source transfer dialog'),
    code.indexOf('$("#addCatBtn").addEventListener')), context);
  const click = (id) => $(id).handlers.click();
  const choose = async (text, name = 'sources.opml') => {
    const file = { name, text: async () => text };
    await $('#importFile').handlers.change({ target: { files: [file], value: name } });
    return file;
  };
  return { $, calls, click, choose, context };
}

test('OPML url fallback is submitted as original file only after confirmation', async () => {
  const { $, calls, click, choose } = setup();
  click('#ioBtn');
  const file = await choose('<opml><body><outline url="https://example.org"/></body></opml>', '<unsafe>.xml');
  assert.equal(calls.length, 0);
  assert.equal($('#importFilename').textContent, 'Ready to import: <unsafe>.xml');
  await click('#sourcesImportConfirm');
  assert.equal(calls[0].path, '/sources/import.opml');
  assert.equal(calls[0].options.body, file);
  assert.match($('#sourcesStatus').textContent, /1 added, 2 updated, 3 skipped/);
  assert.deepEqual(calls.slice(1), ['sidebar', 'items']);
  click('#sourcesClose');
  assert.equal($('#ioBtn').focused, true);
});

test('legacy JSON arrays and documents are detected regardless of extension', async () => {
  for (const text of ['[{"url":"https://example.org"}]', '{"sources":[{"url":"https://example.org"}]}']) {
    const { calls, click, choose } = setup();
    await choose(text);
    await click('#sourcesImportConfirm');
    assert.equal(calls[0].path, '/sources/import');
    assert.deepEqual(JSON.parse(calls[0].options.body), { sources: [{ url: 'https://example.org' }] });
  }
});

test('cancel, empty and malformed JSON files perform no writes', async () => {
  const { $, calls, click, choose } = setup();
  await choose('[{"url":"https://example.org"}]');
  click('#sourcesImportCancel');
  await click('#sourcesImportConfirm');
  for (const text of ['', '[]', '{broken', '{"sources":[null]}', '{}']) {
    await choose(text);
    await click('#sourcesImportConfirm');
    assert.ok($('#sourcesStatus').textContent);
  }
  assert.equal(calls.length, 0);
});

test('failed imports retain confirmation for retry and block duplicate clicks', async () => {
  const { $, calls, click, choose, context } = setup();
  let reject;
  context.api = () => new Promise((_resolve, fail) => { reject = fail; calls.push('request'); });
  await choose('<opml/>');
  const request = click('#sourcesImportConfirm');
  await click('#sourcesImportConfirm');
  assert.equal(calls.length, 1);
  assert.equal($('#sourcesClose').disabled, true);
  reject(new Error('Invalid OPML'));
  await request;
  assert.equal($('#sourcesClose').disabled, false);
  assert.match($('#sourcesStatus').textContent, /Invalid OPML/);
  assert.equal($('#importConfirmation').classList.contains('hidden'), false);
  assert.equal($('#sourcesImportConfirm').focused, true);
});

test('export downloads OPML without a format selector', async () => {
  const { $, calls, click } = setup();
  await click('#sourcesExport');
  assert.match(calls[0].download, /^newsreader-sources-\d{4}-\d{2}-\d{2}\.opml$/);
  assert.equal($('#sourcesStatus').textContent, 'Sources exported as OPML.');
});

test('view refresh failure does not misreport a successful import', async () => {
  const { $, click, choose, context } = setup();
  context.loadSidebar = async () => { throw new Error('Network unavailable'); };
  await choose('<opml/>');
  await click('#sourcesImportConfirm');
  assert.match($('#sourcesStatus').textContent, /Sources imported, but the view could not refresh/);
  assert.equal($('#importConfirmation').classList.contains('hidden'), true);
});

test('failed export enables controls again without downloading', async () => {
  const { $, calls, click, context } = setup();
  context.fetch = async () => ({ ok: false, status: 503 });
  await click('#sourcesExport');
  assert.equal(calls.length, 0);
  assert.match($('#sourcesStatus').textContent, /Export failed \(503\)/);
  assert.equal($('#sourcesExport').disabled, false);
});

test('reported refresh failures show a reload warning', async () => {
  const { $, click, choose, context } = setup();
  context.loadSidebar = async () => false;
  await choose('<opml/>');
  await click('#sourcesImportConfirm');
  assert.match($('#sourcesStatus').textContent, /Sources imported, but the view could not refresh/);
});

test('sidebar loader reports caught network errors to transfer callers', async () => {
  const code = readFileSync(join(__dirname, '../web/app.js'), 'utf8');
  const context = vm.createContext({
    api: async () => { throw new Error('Offline'); }, toast() {},
  });
  vm.runInContext(code.slice(code.indexOf('async function loadSidebar()'),
    code.indexOf('function renderSidebar()')), context);
  assert.equal(await context.loadSidebar(), false);
});

test('Escape is blocked during requests and available again afterward', async () => {
  const { $, click, choose, context } = setup();
  let resolve;
  context.api = () => new Promise((done) => { resolve = done; });
  await choose('<opml/>');
  const request = click('#sourcesImportConfirm');
  let blocked = false;
  $('#sourcesDialog').handlers.cancel({ preventDefault() { blocked = true; } });
  assert.equal(blocked, true);
  resolve({ created: 0, updated: 0, skipped: 0 });
  await request;
  blocked = false;
  $('#sourcesDialog').handlers.cancel({ preventDefault() { blocked = true; } });
  assert.equal(blocked, false);
});
