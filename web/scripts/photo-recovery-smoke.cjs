// Interaction checks with stubbed API responses; never calls a paid model.
// Requires Playwright (or the desktop runtime's NODE_PATH) and a running web app.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.MUSE_TEST_URL || 'http://127.0.0.1:3000';
const image = process.env.MUSE_TEST_IMAGE ? fs.readFileSync(process.env.MUSE_TEST_IMAGE) : Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function waitFor(check) {
  for (let i = 0; i < 50; i++) {if (await check()) return; await pause(100);}
  throw Error('State did not settle');
}
(async () => {
  const browser = await chromium.launch({headless: true, ...(process.env.MUSE_CHROME ? {executablePath: process.env.MUSE_CHROME} : {})});
  try {
    for (const width of [390, 1280]) {
      const page = await browser.newPage({viewport: {width, height: 900}});
      const errors = [], actions = [], chats = [], uploads = [];
      page.on('pageerror', error => errors.push(error.message));
      let sequence = 0, mode = 'candidates';
      const works = ['a', 'b', 'c'].map(id => ({id, title: `Fixture ${id}`, display_title: `测试作品 ${id}`, image_url: '/fixture.jpg', source_url: 'https://example.org/' + id}));
      await page.route('**/fixture.jpg', route => route.fulfill({contentType: process.env.MUSE_TEST_IMAGE ? 'image/jpeg' : 'image/png', body: image}));
      await page.route('**/api/museum/**', async route => {
        const request = route.request(), endpoint = new URL(request.url()).pathname.split('/').at(-1);
        let result;
        if (endpoint === 'health') result = {model_configured: true, storage: 'test', corpus_count: 3};
        else if (endpoint === 'objects') result = works;
        else if (endpoint === 'sessions') result = {token: 'local-test-only'};
        else if (endpoint === 'recognize') {
          uploads.push(request.postDataBuffer()?.toString() || '');
          const retake = uploads.at(-1).includes('parent_trace_id') ? 1 : 0;
          result = {trace_id: 'p' + ++sequence, retake_count: retake,
            status: mode === 'service' ? 'service_unavailable' : 'needs_confirmation',
            match_state: mode === 'candidates' ? 'uncertain' : 'no_reliable_match', message: '请对照作品',
            candidates: mode === 'candidates' ? works : [], similar_candidates: mode === 'similar' ? works.slice(0, 1) : [],
            next_steps: ['补拍顶部和底座。']};
        } else if (endpoint === 'photo-actions') {actions.push(request.postDataJSON()); result = {saved: true};}
        else if (endpoint === 'chat') {chats.push(request.postDataJSON()); result = {trace_id: 't' + chats.length, status: 'answered', mode: 'brief', answer: '本地模拟讲解', sources: [], claims: []};}
        else if (endpoint === 'close') result = {closed: true};
        else throw Error('Unexpected API call: ' + endpoint);
        await route.fulfill({contentType: 'application/json', body: JSON.stringify(result)});
      });
      await page.goto(base);
      await page.getByRole('button', {name: '拍照', exact: true}).waitFor();
      await waitFor(() => page.getByRole('button', {name: '拍照', exact: true}).isEnabled());
      const upload = async () => {
        await waitFor(() => page.getByRole('button', {name: '拍照', exact: true}).isEnabled());
        await page.locator('input[type=file]').last().setInputFiles({name: 'fixture.jpg', mimeType: 'image/jpeg', buffer: image});
        await page.getByRole('button', {name: '发送 ↑', exact: true}).click();
        await waitFor(() => page.getByRole('button', {name: '新对话', exact: true}).isEnabled());
      };
      const last = () => page.locator('.photo-result').last();
      await upload();
      assert.equal(await last().locator('.artwork-candidate').count(), 2);
      assert.equal(chats.length, 0, 'No narration before visitor choice');
      await last().getByRole('button', {name: '都不是', exact: true}).click();
      await last().getByText('已记录这些候选都不是。', {exact: false}).waitFor();
      assert.equal(await last().getByRole('button', {name: '就是这件，继续聊'}).first().isDisabled(), true);
      await last().getByRole('button', {name: '补拍全貌或展签', exact: true}).click();
      await page.getByRole('button', {name: '取消补拍／换件作品', exact: true}).waitFor();
      await upload();
      assert(uploads.at(-1).includes('parent_trace_id'));
      assert.equal(await last().getByRole('button', {name: '补拍全貌或展签', exact: true}).count(), 0);
      await last().getByText('已经补拍过一次。', {exact: false}).waitFor();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      if (process.env.MUSE_SCREENSHOTS) {
        fs.mkdirSync(process.env.MUSE_SCREENSHOTS, {recursive: true});
        await last().scrollIntoViewIfNeeded();
        await page.screenshot({path: path.join(process.env.MUSE_SCREENSHOTS, `photo-recovery-${width}.png`)});
      }
      await last().getByRole('button', {name: '输入名称或展签文字', exact: true}).click();
      await waitFor(() => page.locator('#question').evaluate(el => document.activeElement === el));
      await page.locator('#question').fill('按名称寻找');
      await page.getByRole('button', {name: '发送 ↑', exact: true}).click();
      await waitFor(() => chats.length === 1);
      assert.equal(chats.at(-1).object_id, '');
      await page.getByRole('button', {name: '新对话', exact: true}).click();
      mode = 'service'; await upload();
      await last().getByText('识别服务暂不可用', {exact: true}).waitFor();
      assert.equal(await last().locator('.artwork-candidate').count(), 0);
      await last().getByRole('button', {name: '浏览馆藏', exact: true}).click();
      await waitFor(() => page.locator('#collection-search').evaluate(el => document.activeElement === el));
      await page.getByRole('button', {name: '收起', exact: true}).click();
      mode = 'similar'; await upload();
      await last().getByRole('button', {name: '了解这件相似馆藏', exact: true}).click();
      await waitFor(() => chats.length === 2);
      assert.equal(actions.at(-1).action, 'view_similar');
      assert(chats.at(-1).query.includes('不代表我确认'));
      mode = 'candidates'; await upload();
      await last().getByRole('button', {name: '就是这件，继续聊'}).first().click();
      await waitFor(() => chats.length === 3);
      assert.equal(actions.at(-1).action, 'confirm');
      assert.equal(chats.at(-1).object_id, 'a');
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({width, passed: true, photoRequests: uploads.length, actions: actions.map(a => a.action), narrationRequiresChoice: true}));
      await page.close();
    }
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
