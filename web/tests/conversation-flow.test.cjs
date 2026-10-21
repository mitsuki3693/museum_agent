const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const works = [
  {id: 'old-vase', title: 'Old Vase', image_url: '/old.jpg'},
  {id: 'new-chair', title: 'New Chair', image_url: '/new.jpg'},
];

function response(selected, extra = {}) {
  return {
    trace_id: 'trace', status: 'answered', answer: '有依据的回答', mode: 'brief',
    claims: [], sources: [],
    conversation: {selected_object_id: selected, selection_reset: false, route: 'followup'},
    ...extra,
  };
}

// Execute Home's real event handlers and outgoing requests, not a replica of
// its selection logic. Child components are placeholders so browser APIs and
// provider calls remain outside this deterministic state-transition test.
async function mount() {
  const slots = [], effects = [], requests = [], replies = [], photoReplies = [];
  const apiCalls = [];
  let cursor = 0, nextId = 0;
  const components = Object.fromEntries([
    'ChatComposer', 'CollectionPicker', 'ArtworkCandidate', 'PhotoResultView', 'RoutePanel', 'PagesVisitor',
  ].map(name => [name, function Placeholder() {}]));
  const hooks = {
    useState(initial) {
      const i = cursor++;
      if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial;
      return [slots[i], value => {slots[i] = typeof value === 'function' ? value(slots[i]) : value;}];
    },
    useRef(initial) { const i = cursor++; return slots[i] ??= {current: initial}; },
    useEffect(fn, deps) {
      const i = cursor++, old = slots[i];
      if (!old || deps.some((value, j) => value !== old.deps[j])) {
        effects.push(() => {old?.cleanup?.(); slots[i] = {deps, cleanup: fn()};});
      }
    },
  };
  const network = {
    async museumApi(endpoint, options) {
      apiCalls.push({endpoint, options});
      if (endpoint === 'health') return {model_configured: true, storage: 'memory', corpus_count: works.length};
      if (endpoint === 'objects') return works;
      if (endpoint === 'sessions') return {token: 'session-token'};
      if (endpoint === 'photo-actions') return {saved: true};
      throw new Error('Unexpected API endpoint: ' + endpoint);
    },
    async askMuseum(token, request) {
      requests.push({token, ...JSON.parse(JSON.stringify(request))});
      assert(replies.length, 'Every outgoing chat request needs an explicit response fixture');
      return replies.shift();
    },
    async identifyMuseumPhoto() {
      assert(photoReplies.length, 'Every photo request needs an explicit response fixture');
      return photoReplies.shift();
    },
  };
  const context = vm.createContext({
    exports: {},
    window: {speechSynthesis: {cancel() {}}},
    document: {getElementById() {return {focus() {}};}, querySelector() {return {scrollIntoView() {}};}},
    requestAnimationFrame(fn) {fn();},
    URL: {createObjectURL() {return 'blob:local-test-photo';}, revokeObjectURL() {}},
    require(id) {
      if (id === 'react') return hooks;
      if (id === '@/lib/site-mode') return {staticDemo:false};
      if (id === '@/lib/museum-api') return network;
      if (id === '@/lib/museum-types') return {workName: work => work.title};
      if (id === '@/lib/request-id') return {createRequestId: () => 'request-' + ++nextId};
      if (id === '@/components/MuseumSpeech') return {ListenButton: function ListenButton() {}};
      if (id.startsWith('@/components/')) {
        const name = id.slice('@/components/'.length);
        assert(components[name], 'Unmocked component: ' + name);
        return {default: components[name]};
      }
      return require(id);
    },
  });
  vm.runInContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/app/page.tsx'), 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020},
  }).outputText, context);
  function render() {
    cursor = 0;
    const root = context.exports.default();
    // The default entry chooses static vs live; exercise the real live child.
    const tree = typeof root.type === 'function' ? root.type(root.props) : root;
    while (effects.length) effects.shift()();
    return tree;
  }
  function descendants(node) {
    if (Array.isArray(node)) return node.flatMap(descendants);
    if (!node || typeof node !== 'object') return [];
    return [node, ...descendants(node.props?.children)];
  }
  function children(name) {return descendants(render()).filter(node => node.type === components[name]);}
  function child(name) {
    const matches = children(name);
    assert.equal(matches.length, 1, 'Expected one ' + name);
    return matches[0].props;
  }
  async function settle() {
    // UI handlers intentionally do not return their asynchronous work.
    await new Promise(resolve => setImmediate(resolve));
    render();
    await new Promise(resolve => setImmediate(resolve));
  }
  render();
  await settle();
  return {
    requests, replies, photoReplies, apiCalls, child, children, settle,
    async choose(id, result = response(id)) {
      replies.push(result);
      child('CollectionPicker').onChoose(id);
      await settle();
    },
    async send(text, result) {
      replies.push(result);
      child('ChatComposer').onSend(text);
      await settle();
    },
  };
}

test('new-work response clears the old selection before the next question', async () => {
  const ui = await mount();
  await ui.choose('old-vase');
  assert.equal(ui.child('ChatComposer').selectedName, 'Old Vase');
  const discovery = response(null, {
    status: 'needs_confirmation', candidates: [works[1]],
    conversation: {route: 'accession', selected_object_id: null, selection_reset: true},
  });
  await ui.send('换一件，请找 C.615-1925。', discovery);
  assert.equal(ui.requests.at(-1).object_id, 'old-vase');
  assert.equal(ui.child('ChatComposer').selectedName, '');
  await ui.send('还有别的候选吗？', discovery);
  assert.equal(ui.requests.at(-1).object_id, '');
  assert.equal(ui.child('ChatComposer').selectedName, '');
});

test('confirming a candidate resumes the specific question, then follows the new work', async () => {
  const ui = await mount();
  const continuation = '这件作品是什么材质？';
  await ui.send('C.615-1925 是什么材质？', response(null, {
    status: 'needs_confirmation', candidates: [works[1]], continuation_query: continuation,
    conversation: {route: 'accession', selected_object_id: null, selection_reset: true},
  }));
  ui.replies.push(response('new-chair'));
  ui.child('ArtworkCandidate').onConfirm('new-chair');
  await ui.settle();
  assert.equal(ui.requests.at(-1).query, continuation);
  assert.equal(ui.requests.at(-1).action, 'question');
  assert.equal(ui.requests.at(-1).object_id, 'new-chair');
  assert.equal(ui.child('ChatComposer').selectedName, 'New Chair');
  await ui.send('它是什么年代的？', response('new-chair'));
  assert.equal(ui.requests.at(-1).object_id, 'new-chair');
});

test('comparison responses retain the selected work for subsequent followups', async () => {
  const ui = await mount();
  await ui.choose('old-vase');
  await ui.send('这件和 C.615-1925 有什么不同？', response('old-vase', {
    conversation: {route: 'comparison', selected_object_id: 'old-vase', selection_reset: false},
  }));
  assert.equal(ui.child('ChatComposer').selectedName, 'Old Vase');
  await ui.send('还是讲讲它的材质。', response('old-vase'));
  assert.equal(ui.requests.at(-1).object_id, 'old-vase');
});

test('viewing and discussing a similar photo candidate preserves the identity boundary', async () => {
  const ui = await mount();
  ui.photoReplies.push({
    trace_id: 'photo-trace', status: 'not_matched', match_state: 'unmatched',
    message: '没有可靠匹配', candidates: [], similar_candidates: [works[0]],
  });
  ui.child('ChatComposer').onSend('帮我看看', {name: 'local-fixture.jpg'});
  await ui.settle();
  const similar = response('old-vase', {
    photo_selection: {action: 'view_similar', object_id: 'old-vase'},
    context_notice: '只是查看相似馆藏，不代表确认照片身份。',
  });
  ui.replies.push(similar);
  await ui.child('PhotoResultView').onAction('view_similar', 'old-vase');
  await ui.settle();
  assert.equal(ui.child('ChatComposer').similar, true);
  await ui.send('它是什么年代？', similar);
  assert.equal(ui.requests.at(-1).object_id, 'old-vase');
  assert.equal(ui.child('ChatComposer').similar, true);
  assert.equal(ui.child('ChatComposer').numberClue, false);
  await ui.send('请找 New Chair。', response(null, {
    status: 'needs_confirmation', candidates: [works[1]],
    conversation: {route: 'title', selected_object_id: null, selection_reset: true},
  }));
  assert.equal(ui.child('ChatComposer').selectedName, '');
  assert.equal(ui.child('ChatComposer').similar, false);
});

test('explicit unselection is sent as an empty object and remains cleared', async () => {
  const ui = await mount();
  await ui.choose('old-vase');
  ui.child('ChatComposer').onUnselect();
  assert.equal(ui.child('ChatComposer').selectedName, '');
  await ui.send('不是这件，我想找蓝色的。', response(null, {
    status: 'needs_confirmation', candidates: [works[1]],
    conversation: {route: 'discovery', selected_object_id: null, selection_reset: true},
  }));
  assert.equal(ui.requests.at(-1).object_id, '');
  assert.equal(ui.child('ChatComposer').selectedName, '');
});

test('older responses without conversation metadata do not erase an explicit selection', async () => {
  const ui = await mount();
  await ui.choose('old-vase');
  const legacy = response(null);
  delete legacy.conversation;
  await ui.send('它是什么材质？', legacy);
  assert.equal(ui.child('ChatComposer').selectedName, 'Old Vase');
});
