const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const {renderToStaticMarkup} = require('react-dom/server');

function load(name) {
  const context = vm.createContext({exports: {}, require: id => id.startsWith('./') ? load(id.slice(2)) : require(id)});
  vm.runInContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/components', name + '.tsx'), 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020},
  }).outputText, context);
  return context.exports;
}
const View = load('PhotoResultView').default;
const candidates = ['a', 'b', 'c'].map(id => ({id, title: 'Work ' + id}));
const result = {trace_id: 'photo', status: 'needs_confirmation', match_state: 'uncertain',
  message: '请确认', candidates, next_steps: ['补拍顶部'], retake_count: 0};
const render = (value = result, action) => renderToStaticMarkup(React.createElement(View, {
  result: value, action, items: [], busy: false, onAction() {},
}));

test('at most two identity choices and an explicit rejection escape', () => {
  const html = render();
  assert.equal((html.match(/就是这件，继续聊/g) || []).length, 2);
  assert(!html.includes('Work c'));
  for (const text of ['都不是', '补拍全貌或展签', '输入名称或展签文字', '浏览馆藏']) assert(html.includes(text));
});
test('one retake removes the endless retake suggestion but keeps alternatives', () => {
  const html = render({...result, retake_count: 1});
  assert(!html.includes('补拍全貌或展签') && !html.includes('补拍顶部'));
  assert(html.includes('已经补拍过一次') && html.includes('输入名称或展签文字'));
});
test('service errors never masquerade as unmatched photos or offer stale candidates', () => {
  const html = render({...result, status: 'service_unavailable', match_state: 'no_reliable_match', retake_count: 1});
  assert(html.includes('识别服务暂不可用') && html.includes('稍后重新发送照片'));
  assert(!html.includes('暂未找到可靠匹配') && !html.includes('就是这件，继续聊') && !html.includes('补拍顶部'));
});
test('similarity only offers catalogue browsing, never identity confirmation', () => {
  const html = render({...result, candidates: [], similar_candidates: candidates});
  assert(!html.includes('就是这件，继续聊'));
  assert.equal((html.match(/了解这件相似馆藏/g) || []).length, 2);
  assert(html.includes('不代表认出了你的照片'));
});
test('rejected cards are disabled while recovery remains possible', () => {
  const html = render(result, 'reject');
  assert.equal((html.match(/disabled=""/g) || []).length, 2);
  assert(html.includes('已记录这些候选都不是') && html.includes('浏览馆藏'));
});
test('submitted choices cannot fire a second action from the same card', () => {
  for (const action of ['confirm', 'view_similar', 'view_number', 'retry', 'search', 'browse']) {
    const html = render(result, action);
    assert(!html.includes('class="photo-recovery-actions"'));
    assert.equal((html.match(/disabled=""/g) || []).length, 2);
  }
});

test('number conflicts offer labelled browsing, not visual confirmation or generic similarity', () => {
  const html = render({...result, candidates: [], number_candidates: [candidates[0]], similar_candidates: [candidates[1]]});
  assert(html.includes('编号有线索，照片尚未确认') && html.includes('按编号找到'));
  assert(html.includes('查看这件馆藏') && !html.includes('就是这件，继续聊'));
  assert(!html.includes('Work b') && !html.includes('相似作品'));
  assert(html.includes('补拍正面') && html.includes('都不是'));
  const closed = render({...result, candidates: [], number_candidates: [candidates[0]]}, 'view_number');
  assert(closed.includes('原照片的身份仍未确认') && !closed.includes('class="photo-recovery-actions"'));
});

test('service failure does not show stale number clues', () => {
  const html = render({...result, status: 'service_unavailable', number_candidates: candidates});
  assert(!html.includes('按编号找到') && !html.includes('查看这件馆藏'));
});
