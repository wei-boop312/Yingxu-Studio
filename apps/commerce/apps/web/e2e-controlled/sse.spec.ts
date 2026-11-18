import {test, expect, type Page} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {readFileSync, writeFileSync, mkdirSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {createHash} from 'node:crypto';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const py = path.join(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');

async function setup(page: Page) {
  const provision = JSON.parse(execFileSync(py, ['scripts/create_e2e_account.py'], {cwd: root, encoding: 'utf8'}));
  const account = JSON.parse(readFileSync(provision.account_file, 'utf8'));
  await page.goto('/');
  await page.getByLabel('邮箱', {exact: true}).fill(account.email);
  await page.getByLabel('密码', {exact: true}).fill(account.password);
  await page.getByRole('button', {name: '进入工作台'}).click();
  await expect(page.getByRole('heading', {name: '视觉工作台', exact: true})).toBeVisible();
  const headers = {'Origin': 'http://127.0.0.1:5173', 'X-CSRF-Token': (await page.context().cookies()).find(c => c.name === 'visual_csrf')!.value};
  const product = await page.request.post('/api/v1/products', {headers, data: {name: 'SSE 游标验收产品', category: 'generic'}});
  expect(product.status()).toBe(201);
  const data = readFileSync(new URL('../e2e/fixtures/product.png', import.meta.url));
  const intent = await page.request.post('/api/v1/assets/upload-intents', {headers, data: {product_id: (await product.json()).id, filename: 'fixture.png', mime: 'image/png', size: data.length}});
  expect(intent.status()).toBe(201);
  const u = await intent.json();
  expect((await page.request.put(u.upload_url, {data, headers: u.headers})).status()).toBe(200);
  expect((await page.request.post('/api/v1/assets/upload-intents/' + u.intent_id + '/complete', {headers})).status()).toBe(200);
  await page.reload();
  await expect(page.getByText('已归档原图', {exact: true})).toBeVisible();await page.getByLabel('细节保护',{exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('无保护 · 不回贴原图像素',{exact:true}).click();
  return account;
}

async function submit(page: Page) {
  await page.getByRole('button', {name: '获取报价并继续'}).click();
  const created = page.waitForResponse(r => r.url().endsWith('/api/v1/tasks') && r.request().method() === 'POST' && r.status() === 201);
  await page.getByRole('dialog', {name: '确认本次生成'}).getByRole('button', {name: '确认并创建任务', exact: true}).click();
  const task = await (await created).json();
  await expect(page.getByRole('button', {name: '取消排队任务', exact: true})).toBeVisible();
  return task;
}

test('真实 SSE 刷新游标、自动重连、重复和旧版本事件以及账号任务隔离', async ({page, browser}) => {
  test.setTimeout(120000);
  expect(process.env.CONTROLLED_FAKE_BROWSER, 'owned consumers must be paused').toBe('1');
  expect(process.env.SSE_API_LOG, 'owned API structured connection log is required').toBeTruthy();
  const apiLog = process.env.SSE_API_LOG!;
  const serverEvents = (offset: number) => readFileSync(apiLog, 'utf8').slice(offset).split('\n').flatMap(line => {
    const start = line.indexOf('{"event":');
    if (start < 0) return [];
    try { const item = JSON.parse(line.slice(start)); return item.event === 'task_event_stream_open' ? [item] : []; } catch { return []; }
  });
  // Keep the native EventSource and real network. Only observe frames/fetches;
  // duplicate/stale fault frames are explicitly injected after a real PG event.
  await page.addInitScript(() => {
    const probe: any = {sources: [], frames: [], reads: {}, resets: 0};
    (window as any).__sseProbe = probe;
    const NativeEventSource = window.EventSource;
    window.EventSource = class extends NativeEventSource {
      constructor(url: string | URL, options?: EventSourceInit) {
        super(url, options);
        probe.sources.push(this);
        this.addEventListener('task.updated', event => probe.frames.push({id: event.lastEventId, data: event.data, url: this.url}));
        this.addEventListener('stream.reset', () => probe.resets++);
      }
    };
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (...args: Parameters<typeof fetch>) => {
      const input = args[0];
      const url = new URL(input instanceof Request ? input.url : String(input), location.href);
      const method = args[1]?.method ?? (input instanceof Request ? input.method : 'GET');
      if (method.toUpperCase() === 'GET' && /^\/api\/v1\/tasks\/[^/]+$/.test(url.pathname)) probe.reads[url.pathname] = (probe.reads[url.pathname] || 0) + 1;
      return nativeFetch(...args);
    };
  });
  const requests: Array<{task: string, cursor: string | null}> = [];
  let taskPosts = 0;
  page.on('request', request => {
    const url = new URL(request.url());
    if (request.method() === 'POST' && url.pathname === '/api/v1/tasks') taskPosts++;
    const match = url.pathname.match(/^\/api\/v1\/tasks\/([^/]+)\/events$/);
    if (match) requests.push({task: match[1], cursor: url.searchParams.get('cursor')});
  });
  const account = await setup(page);
  const first = await submit(page);
  const key = 'visual:event-cursor:v1:' + encodeURIComponent(account.user_id) + ':' + encodeURIComponent(first.id);
  await expect.poll(() => page.evaluate(k => sessionStorage.getItem(k), key)).not.toBeNull();
  const saved = (await page.evaluate(k => sessionStorage.getItem(k), key))!;
  expect(BigInt(saved)).toBeGreaterThan(0n);
  await page.reload();
  await expect(page.getByRole('button', {name: '取消排队任务', exact: true})).toBeVisible();
  await expect.poll(() => requests.some(r => r.task === first.id && r.cursor === saved)).toBe(true);
  // Since the cursor is current, the replay has no task.updated frame. Clearing
  // the persisted cursor and reloading obtains the real stored frame to inject.
  await page.evaluate(k => sessionStorage.removeItem(k), key);
  await page.reload();
  await expect.poll(() => page.evaluate(() => (window as any).__sseProbe.frames.length)).toBeGreaterThan(0);
  const injected = await page.evaluate(task => {
    const probe = (window as any).__sseProbe;
    const frame = probe.frames.find((f: any) => JSON.parse(f.data).task_id === task);
    const source = probe.sources.find((s: EventSource) => new URL(s.url).pathname.endsWith('/' + task + '/events'));
    const url = '/api/v1/tasks/' + task;
    const before = probe.reads[url] || 0;
    source.dispatchEvent(new MessageEvent('task.updated', {lastEventId: frame.id, data: frame.data}));
    const afterDuplicate = probe.reads[url] || 0;
    const staleId = String(BigInt(frame.id) + 1n);
    // Valid same-task/revision payload with a newer transport ID: revision
    // suppression must work independently of ID deduplication.
    source.dispatchEvent(new MessageEvent('task.updated', {lastEventId: staleId, data: frame.data}));
    return {before, afterDuplicate, afterStale: probe.reads[url] || 0, staleId, originalId: frame.id, payload: frame.data};
  }, first.id);
  expect(injected.afterDuplicate).toBe(injected.before);
  expect(injected.afterStale).toBe(injected.before);
  expect(await page.evaluate(k => sessionStorage.getItem(k), key)).toBe(injected.staleId);
  // Synthetic cursor lies beyond persisted history. Real server retention
  // recovery must reset it and replay the real event, without creating a task.
  await page.reload();
  await expect.poll(() => page.evaluate(() => (window as any).__sseProbe.resets)).toBeGreaterThan(0);
  await expect.poll(() => page.evaluate(k => sessionStorage.getItem(k), key)).toBe(saved);
  // Browser offline emulation does not reliably terminate an established SSE
  // socket. Inject a real HTTP EOF after replaying the captured PG frame once;
  // the native EventSource must reconnect to the real API with its own header.
  let eofInjected = false;
  const reconnectLogStart = readFileSync(apiLog, 'utf8').length;
  await page.route('**/api/v1/tasks/' + first.id + '/events*', async route => {
    if (eofInjected) { await route.continue(); return; }
    eofInjected = true;
    await route.fulfill({status: 200, contentType: 'text/event-stream', headers: {'Cache-Control': 'no-store'}, body: 'retry: 1000\nid: ' + saved + '\nevent: task.updated\ndata: ' + injected.payload + '\n\n'});
  });
  await page.reload();
  await expect.poll(() => eofInjected).toBe(true);
  // Chromium adds this header below the Playwright request interception layer;
  // use the actual API's authorized, normalized trace as authoritative evidence.
  const resumed = () => serverEvents(reconnectLogStart).find(item => item.task_id === first.id && item.cursor_source === 'last-event-id' && item.cursor === saved);
  await expect.poll(() => !!resumed(), {timeout: 15000}).toBe(true);
  const serverReconnect = resumed();
  await expect(page.getByText('实时连接', {exact: true})).toBeVisible({timeout: 20000});
  await page.getByRole('button', {name: '取消排队任务', exact: true}).click();
  await expect(page.getByText('排队任务已取消，冻结积分已退回。', {exact: true})).toBeVisible();
  const second = await submit(page);
  await expect.poll(() => requests.some(r => r.task === second.id)).toBe(true);
  expect(requests.find(r => r.task === second.id)!.cursor).toBeNull();
  const secondKey = 'visual:event-cursor:v1:' + encodeURIComponent(account.user_id) + ':' + encodeURIComponent(second.id);
  await expect.poll(() => page.evaluate(k => sessionStorage.getItem(k), secondKey)).not.toBeNull();
  expect(secondKey).not.toBe(key);
  await page.getByRole('button', {name: '取消排队任务', exact: true}).click();
  await expect(page.getByText('排队任务已取消，冻结积分已退回。', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: '退出登录', exact: true}).click();
  await expect(page.getByRole('heading', {name: '登录你的工作台'})).toBeVisible();
  const other = await setup(page);
  const third = await submit(page);
  await expect.poll(() => requests.some(r => r.task === third.id)).toBe(true);
  expect(requests.find(r => r.task === third.id)!.cursor).toBeNull();
  const otherKey = 'visual:event-cursor:v1:' + encodeURIComponent(other.user_id) + ':' + encodeURIComponent(third.id);
  await expect.poll(() => page.evaluate(k => sessionStorage.getItem(k), otherKey)).not.toBeNull();
  expect(other.user_id).not.toBe(account.user_id);
  expect(await page.evaluate(k => sessionStorage.getItem(k), key)).not.toBeNull();
  expect((await page.request.get('/api/v1/tasks/' + first.id)).status()).toBe(404);
  await page.getByRole('button', {name: '取消排队任务', exact: true}).click();
  await expect(page.getByText('排队任务已取消，冻结积分已退回。', {exact: true})).toBeVisible();
  expect(taskPosts).toBe(3);
  const proof = {
    result: 'PASS', commit: execFileSync('git', ['rev-parse', 'HEAD'], {cwd: root, encoding: 'utf8'}).trim(),
    observed_at: new Date().toISOString(), paid_calls: 0, browser: browser.version(),
    scope: 'production React app + native EventSource/PG HTTP replay; duplicate/stale frames and one finite replay/EOF injected at browser boundaries; reconnect forwarded to real API',
    deduplicated_events: true, ignored_stale_revisions: true, cursor_restored_after_reload: true,
    cursor_namespaced_by_user_and_task: true, automatic_last_event_header: true, server_reset_recovered: true,
    task_posts: taskPosts, injection_snapshot_reads: injected, event_requests: requests,
    server_reconnect: serverReconnect,
    test_sha256: createHash('sha256').update(readFileSync(fileURLToPath(import.meta.url))).digest('hex')
  };
  mkdirSync(path.join(root, 'work/ci-public'), {recursive: true});
  writeFileSync(path.join(root, 'work/ci-public/frontend-sse-cursor.json'), JSON.stringify(proof, null, 2) + '\n');
});
