const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const ts = require('typescript');

// Run the production network module in a browser-like context, including LAN HTTP.
function client(browserCrypto, responses = [{ok: true, data: {status: 'answered'}}]) {
  const calls = [];
  const context = vm.createContext({crypto: browserCrypto, Uint8Array, exports: {},
    fetch: async (url, options) => {
      calls.push({url, ...options, body: JSON.parse(options.body)});
      const response = responses.shift();
      return {ok: response.ok, headers: {get: () => 'application/json'}, json: async () => response.data};
    },
  });
  function load(file) {
    context.exports = {};
    vm.runInContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/lib/', file), 'utf8'), {
      compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2017},
    }).outputText, context);
    return context.exports;
  }
  const ids = load('request-id.ts');
  context.require = name => {assert.equal(name, './request-id'); return ids;};
  return {api: load('museum-api.ts'), calls};
}
const request = {query: '  介绍睡莲  ', object_id: 'artic-16568', mode: 'brief', action: 'narration'};

test('LAN HTTP sends authenticated artwork requests without crypto.randomUUID', async () => {
  const {api, calls} = client({getRandomValues: array => webcrypto.getRandomValues(array)}, [
    {ok: true, data: {status: 'answered'}}, {ok: true, data: {status: 'answered'}},
  ]);
  await api.askMuseum('demo-session', request);
  await api.askMuseum('demo-session', request);
  for (const call of calls) {
    assert.equal(call.url, '/api/museum/chat');
    assert.equal(call.headers.Authorization, 'Bearer demo-session');
    assert.equal(call.body.object_id, request.object_id);
    assert.equal(call.body.query, '介绍睡莲');
    assert.equal(call.body.action, 'narration');
    assert.match(call.body.request_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  }
  assert.notEqual(calls[0].body.request_id, calls[1].body.request_id);
});
test('secure context sends a native UUID', async () => {
  const {api, calls} = client(webcrypto);
  assert.equal((await api.askMuseum('demo-session', request)).status, 'answered');
  assert.equal(calls.length, 1);
  assert.match(calls[0].body.request_id, /^[0-9a-f-]{36}$/);
});
test('retry preserves the message idempotency key and original question', async () => {
  const {api, calls} = client(webcrypto, [
    {ok: false, data: {detail: 'temporarily unavailable'}}, {ok: true, data: {status: 'answered'}},
  ]);
  const retry = {...request, query: '这件作品是谁创作的？', action: 'question', request_id: webcrypto.randomUUID()};
  await assert.rejects(api.askMuseum('demo-session', retry), /temporarily unavailable/);
  await api.askMuseum('demo-session', retry);
  assert.deepEqual(calls[0].body, calls[1].body);
  assert.equal(calls[1].body.query, retry.query);
  assert.equal(calls[1].body.action, 'question');
});

test('route submission preserves time, accessibility and skipped stops', async () => {
  const {api, calls} = client({getRandomValues: array => webcrypto.getRandomValues(array)});
  const route = {minutes: 30, start_id: 'entrance', interests: ['sculpture'], step_free: true, skip_ids: ['g2']};
  await api.askMuseum('route-session', {...request, query: '规划参观路线', action: 'route', route});
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0].body.route)), route);
  assert.equal(calls[0].body.action, 'route');
  assert.equal(calls[0].headers.Authorization, 'Bearer route-session');
});
