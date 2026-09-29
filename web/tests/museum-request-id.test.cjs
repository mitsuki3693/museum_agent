const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const ts = require('typescript');

// Execute the actual page's submit handler, with browser APIs matching LAN HTTP.
// The React setters and network are isolated; the request-building path is real.
async function submit(browserCrypto) {
  const page = fs.readFileSync(path.join(__dirname, '../src/app/page.tsx'), 'utf8');
  const handler = page.slice(page.indexOf(' async function ask('), page.indexOf(' async function clear('));
  assert.ok(handler.includes('request_id'), 'submit handler was not located');
  const calls = [], errors = [], turns = [];
  const context = vm.createContext({
    crypto: browserCrypto, Uint8Array, busy: false, mode: 'brief',
    ensureSession: async () => 'test-session',
    api: async (route, options) => {calls.push({route, body: JSON.parse(options.body)}); return {status: 'answered'};},
    setBusy() {}, setError: error => {if(error) errors.push(error);}, setQuery() {},
    setTurns: update => turns.push(...update([])), exports: {},
  });
  const helper = path.join(__dirname, '../src/lib/request-id.ts');
  if (fs.existsSync(helper)) {
    vm.runInContext(ts.transpileModule(fs.readFileSync(helper, 'utf8'), {
      compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2017},
    }).outputText, context);
    context.createRequestId = context.exports.createRequestId;
  }
  await vm.runInContext(ts.transpileModule(handler + '\nask("介绍睡莲", "artic-16568");', {
    compilerOptions: {target: ts.ScriptTarget.ES2017},
  }).outputText, context);
  return {calls, errors, turns};
}

test('LAN HTTP: selecting an artwork sends chat even without crypto.randomUUID', async () => {
  const browserCrypto = {getRandomValues: array => webcrypto.getRandomValues(array)};
  const ids = [];
  for (let i=0; i<2; i++) {
    const result = await submit(browserCrypto);
    assert.deepEqual(result.errors, [], 'selecting an artwork must not show a crypto error');
    assert.equal(result.calls.length, 1);
    assert.equal(result.calls[0].body.object_id, 'artic-16568');
    assert.equal(result.turns.length, 1);
    ids.push(result.calls[0].body.request_id);
    assert.match(ids[i], /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  }
  assert.notEqual(ids[0], ids[1], 'separate questions must not share an idempotency key');
});

test('secure context: native UUID still sends the selected artwork', async () => {
  const result = await submit(webcrypto);
  assert.deepEqual(result.errors, []);
  assert.equal(result.calls.length, 1);
});
