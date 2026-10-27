// Production SQLite/Inbox/HTTP fixture; synthetic image response enrichment only.
// This checks the packaged reader, not multimodal execution or message delivery.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createInterface} from 'node:readline';
import {testPythonCommand} from '../../../../scripts/test-python.mjs';
import {launchBrowser, loadPlaywright} from '../../../../examples/dashboard-browser-smoke-support.mjs';

const repo = resolve(import.meta.dirname, '../../../..');
const output = resolve(repo, 'output/playwright/delegation-media');
await mkdir(output, {recursive: true});
const fixturePath = resolve(repo, 'examples/personal-workspace-browser/steward_requests_fixture.py');
const command = testPythonCommand(`import runpy; runpy.run_path(${JSON.stringify(fixturePath)}, run_name="__main__")`, {repoRoot: repo});
const child = spawn(command.command, [...command.args, '--status-file', resolve(repo, 'examples/status.example.json'),
  ...(process.env.LOOPX_MEDIA_ASSETS ? ['--assets-dir', process.env.LOOPX_MEDIA_ASSETS] : [])],
  {cwd: repo, env: {...process.env, LOOPX_USAGE_PING: '0'}, stdio: ['pipe', 'pipe', 'pipe']});
let stderr = '';
child.stderr.on('data', value => stderr += value);
const queue = [], waiting = [];
createInterface({input: child.stdout}).on('line', line => {
  try {const value = JSON.parse(line); if (waiting.length) waiting.shift()(value); else queue.push(value);} catch { /* startup banner */ }
});
const next = () => new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(Error(`Fixture timeout: ${stderr}`)), 90000);
  const receive = value => {clearTimeout(timer); resolve(value);};
  if (queue.length) receive(queue.shift()); else waiting.push(receive);
});
async function inspect() {child.stdin.write('inspect\n'); return next();}
let browser, page;
try {
  const ready = await next(); assert.equal(ready.event, 'ready');
  browser = await launchBrowser(loadPlaywright().chromium);
  page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  await page.addInitScript(() => {if (!localStorage.getItem('loopx-pw-locale')) localStorage.setItem('loopx-pw-locale', 'zh-CN');});
  const raster = await page.evaluate(() => {
    const canvas = document.createElement('canvas'); canvas.width = 600; canvas.height = 180;
    const context = canvas.getContext('2d');
    context.fillStyle = '#fafafa'; context.fillRect(0, 0, 600, 180);
    context.fillStyle = '#0070f3'; context.fillRect(32, 32, 220, 116);
    context.fillStyle = '#f5a623'; context.beginPath(); context.arc(430, 90, 58, 0, Math.PI * 2); context.fill();
    return canvas.toDataURL('image/png');
  });
  const writes = [], errors = [];
  page.on('request', request => {if (request.method() !== 'GET') writes.push(new URL(request.url()).pathname);});
  page.on('pageerror', error => errors.push(error.message));
  // Read existing manager history with its executor offline. There is no Lark
  // binding in this fixture, and browsing must not need a model admission.
  await page.route('**/api/chat/app-steward', route => route.fulfill({json: {ok: true, binding_id: null, session_id: null}}));
  await page.route('**/api/chat/capabilities', async route => {
    const response = await route.fetch();
    const value = await response.json();
    for (const adapter of value.adapters ?? []) adapter.available = false;
    return route.fulfill({response, json: value});
  });
  let mode = 'images', release, observed;
  const body = '这是原请求的完整正文。Original request evidence. '.repeat(80) + '完整正文结束。';
  await page.route(`**/api/chat/sessions/${ready.session_id}`, async route => {
    assert.equal(route.request().method(), 'GET');
    if (mode === 'unavailable') return route.fulfill({status: 503, json: {ok: false}});
    const response = await route.fetch(), value = await response.json();
    const source = value.messages.find(message => message.role === 'user');
    source.text = body;
    const image = (id, name, data_url = raster) => ({id, name, data_url, mime_type: 'image/png', size: 2048});
    source.attachments = mode === 'text' ? [] : [image('one', '原始图一.png'), image('two', 'Original image two.png')];
    if (mode === 'broken') source.attachments = [image('broken', '不可读图片.png', 'data:image/png;base64,AA==')];
    value.messages.push({...source, message_id: 'other-turn', turn_id: 'other-turn', text: 'Other turn must stay hidden.',
      attachments: [image('foreign', 'Foreign turn image.png')]});
    if (mode === 'wrong-session') value.session.session_id = 'foreign-session';
    if (mode === 'delayed') {observed(); await new Promise(resolve => release = resolve);}
    await route.fulfill({response, json: value}).catch(() => {});
  });
  await page.goto(ready.origin + '/chat/');
  await page.getByRole('navigation', {name: '管家视图'}).getByRole('button', {name: /^(Chat|对话)$/}).click();
  await page.getByText('复核迁移说明中的示例', {exact: true}).filter({visible: true}).first().waitFor();
  // Let the stored manager admission settle before opening its modal. That
  // source transition deliberately closes readers opened on fallback state.
  await page.waitForTimeout(1000);
  const trigger = page.getByRole('button', {name: /委派，.*条待跟进/});
  await trigger.click();
  const dialog = page.getByRole('dialog', {name: '委派', exact: true});
  const input = dialog.getByRole('searchbox', {name: '搜索委派'});
  await page.waitForFunction(() => document.querySelector('.conversation-requests-body')?.getAttribute('aria-busy') === 'false');
  await page.screenshot({path: resolve(output, 'default-desktop.png')});
  await input.fill('核对账单导出的字段映射');
  await dialog.getByRole('button', {name: '搜索', exact: true}).click();
  const row = dialog.locator('.conversation-request-row');
  await row.locator(':scope > summary').click();
  const original = row.getByText('原对话', {exact: true});
  await original.click();
  const articles = row.locator('article');
  await articles.first().waitFor();
  assert.equal(await articles.first().locator('.personal-md').innerText(), body);
  assert.equal(await row.getByText('Other turn must stay hidden.', {exact: true}).count(), 0);
  const images = row.locator('img');
  if (process.env.LOOPX_MEDIA_BEFORE === '1') {
    assert.equal(await images.count(), 0);
    await articles.first().scrollIntoViewIfNeeded();
    await page.screenshot({path: resolve(output, 'before-desktop.png')});
    console.log('BASELINE: the original full body is present, but both supplied images are absent.');
  } else {
    assert.equal(await images.count(), 2);
    assert.deepEqual(await images.evaluateAll(nodes => nodes.map(node => node.alt)), ['原始图一.png', 'Original image two.png']);
    await images.first().waitFor({state: 'visible'});
    await page.waitForFunction(() => [...document.querySelectorAll('.conversation-request-images img')].every(img => img.complete && img.naturalWidth === 600));
    await images.first().scrollIntoViewIfNeeded();
    await page.screenshot({path: resolve(output, 'images-desktop.png')});
    await page.setViewportSize({width: 390, height: 844});
    await images.first().scrollIntoViewIfNeeded();
    assert.ok(await dialog.evaluate(node => node.scrollWidth <= node.clientWidth + 1));
    assert.ok(await images.evaluateAll(nodes => nodes.every(node => Math.abs(node.clientWidth / node.clientHeight - 600 / 180) < .05)), 'Original images keep their full aspect ratio');
    await page.screenshot({path: resolve(output, 'images-narrow.png')});
    const reopen = async nextMode => {
      await original.click(); mode = nextMode; await original.click();
    };
    await reopen('text'); await articles.first().waitFor(); assert.equal(await images.count(), 0);
    await reopen('broken');
    const broken = row.getByRole('status').filter({hasText: '图片无法显示：不可读图片.png'});
    await broken.waitFor(); await broken.scrollIntoViewIfNeeded();
    await page.screenshot({path: resolve(output, 'image-unavailable.png')});
    await reopen('wrong-session'); await row.getByRole('alert').waitFor(); assert.equal(await images.count(), 0);
    await reopen('unavailable'); await row.getByRole('alert').waitFor();
    await reopen('images'); await images.first().waitFor();
    await original.click(); mode = 'delayed';
    const seen = new Promise(resolve => observed = resolve);
    await original.click(); await seen; await original.click(); release();
    assert.equal(await images.count(), 0, 'A late read cannot reopen a closed original');
    await page.keyboard.press('Escape');
    assert.equal(await trigger.evaluate(node => node === document.activeElement), true);
    await page.evaluate(() => localStorage.setItem('loopx-pw-locale', 'en')); mode = 'images';
    await page.reload();
    await page.getByRole('button', {name: /Delegations,.*awaiting follow-up/}).click();
    const english = page.getByRole('dialog', {name: 'Delegations', exact: true});
    await english.getByRole('searchbox', {name: 'Search delegations'}).fill('核对账单导出的字段映射');
    await english.getByRole('button', {name: 'Search', exact: true}).click();
    await english.locator('.conversation-request-row > summary').first().click();
    await english.getByText('Original conversation', {exact: true}).click();
    await english.getByRole('img', {name: '原始图一.png', exact: true}).waitFor();
    const inspection = await inspect();
    assert.equal(inspection.source_unchanged, true); assert.equal(inspection.posts, 0);
    assert.deepEqual(writes, [], 'Reading originals must not POST or start a Turn');
    assert.deepEqual(errors, []);
    console.log('PASS: packaged original images/full body, same-Turn isolation, text-only, failed image/read, foreign-session denial, retry, late close, focus return, narrow aspect ratio, English and unchanged SQLite/Inbox; zero writes.');
  }
} catch (error) {
  if (page) {await page.screenshot({path: resolve(output, 'failure.png')}).catch(() => {}); await writeFile(resolve(output, 'failure.txt'), await page.locator('body').innerText());}
  throw error;
} finally {await browser?.close(); child.stdin.write('stop\n'); child.stdin.end();}
