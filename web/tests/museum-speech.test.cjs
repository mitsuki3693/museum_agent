const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

// Exercise the actual component handlers with browser event ordering controlled.
function mount() {
  const slots = [], effects = [], recognitions = [], texts = [], timers = new Map();
  let cursor = 0, timerId = 0, disabled = false;
  class Recognition {
    constructor() { recognitions.push(this); }
    start() { this.started = true; }
    stop() { this.stopped = true; }
    abort() { this.aborted = true; }
    result(text) { this.onresult?.({resultIndex: 0, results: [Object.assign([{transcript: text}], {isFinal: true})]}); }
  }
  const hooks = {
    useState(value) { const i = cursor++; if (!(i in slots)) slots[i] = value; return [slots[i], v => {slots[i] = v;}]; },
    useRef(value) { const i = cursor++; return slots[i] ??= {current: value}; },
    useEffect(fn, deps) { const i = cursor++; const old = slots[i];
      if (!old || deps.some((v, j) => v !== old.deps[j])) effects.push(() => {
        old?.cleanup?.(); slots[i] = {deps, cleanup: fn()};
      });
    },
  };
  const context = vm.createContext({exports: {}, window: {SpeechRecognition: Recognition},
    setTimeout(fn, ms) { const id = ++timerId; timers.set(id, {fn, ms}); return id; },
    clearTimeout(id) { timers.delete(id); },
    require(id) { return id === 'react' ? hooks : require(id); },
  });
  vm.runInContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/components/MuseumSpeech.tsx'), 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020},
  }).outputText, context);
  function render() {
    cursor = 0;
    const tree = context.exports.VoiceInput({onText: text => texts.push(text), disabled});
    while (effects.length) effects.shift()();
    return tree.props.children[0];
  }
  render();
  return {recognitions, texts, render, timers,
    click() { render().props.onClick(); },
    disable() { disabled = true; render(); },
    unmount() { for (const slot of slots) slot?.cleanup?.(); },
    fireTimers() { for (const {fn} of [...timers.values()]) fn(); },
  };
}

test('final result without end releases microphone and permits a second recording', () => {
  const ui = mount(); ui.click(); const first = ui.recognitions[0];
  first.result('第一件作品');
  assert.equal(ui.render().props['aria-label'], '语音输入');
  assert.equal(first.aborted, true);
  ui.click(); ui.recognitions[1].result('第二件作品');
  assert.deepEqual(ui.texts, ['第一件作品', '第二件作品']);
  assert.equal(ui.recognitions.length, 2);
});

test('late callbacks from a completed recording cannot alter the next recording', () => {
  const ui = mount(); ui.click(); const first = ui.recognitions[0];
  const lateEnd = first.onend, lateResult = first.onresult;
  first.result('first'); ui.click(); lateEnd();
  lateResult({results: [[{transcript: 'stale'}]]});
  assert.equal(ui.render().props['aria-label'], '停止录音');
  assert.deepEqual(ui.texts, ['first']);
});

test('permission failure permits a fresh user-initiated attempt', () => {
  const ui = mount(); ui.click(); ui.recognitions[0].onerror({error: 'not-allowed'});
  assert.equal(ui.render().props['aria-label'], '语音输入');
  assert.equal(ui.recognitions[0].aborted, true);
  ui.click(); assert.equal(ui.recognitions.length, 2);
});

test('stop without end eventually resets instead of waiting for page refresh', () => {
  const ui = mount(); ui.click(); ui.click();
  assert.equal(ui.recognitions[0].stopped, true);
  assert(ui.timers.size > 0); ui.fireTimers();
  assert.equal(ui.render().props['aria-label'], '语音输入');
  ui.click(); assert.equal(ui.recognitions.length, 2);
});

test('disabling or unmounting releases the active recording', () => {
  for (const action of ['disable', 'unmount']) {
    const ui = mount(); ui.click(); ui[action]();
    assert.equal(ui.recognitions[0].aborted, true);
    assert.equal(ui.recognitions[0].onresult, null);
    assert.equal(ui.timers.size, 0);
  }
});
