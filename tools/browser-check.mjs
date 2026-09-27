/* Headless smoke test for the gallery.

   The server has no browser, so UI regressions used to be invisible here. This
   drives chrome-headless-shell over CDP with a phone-sized viewport, walks the
   page the way a user would, and reports what actually rendered plus every
   console error and failed request.

   Usage: FA_AUTH='user:password' node tools/browser-check.mjs [url] [#album]
   Needs Node 22+ and chrome-headless-shell (path in CHROME, see
   https://googlechromelabs.github.io/chrome-for-testing/).
*/
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';

const CHROME = process.env.CHROME || 'chrome-headless-shell';
const PORT = 9222;
const URL_ = process.argv[2] || 'http://127.0.0.1:8080/';
const HASH = process.argv[3] || '';
const AUTH = 'Basic ' + Buffer.from(process.env.FA_AUTH || '').toString('base64');

const chrome = spawn(CHROME, [
  `--remote-debugging-port=${PORT}`, '--no-sandbox', '--disable-gpu',
  '--hide-scrollbars', '--disable-dev-shm-usage', 'about:blank',
], { stdio: 'ignore' });

const errors = [];
const failures = [];
let id = 0;
const pending = new Map();
let ws;

const send = (method, params = {}) => new Promise((resolve, reject) => {
  const msg = { id: ++id, method, params };
  pending.set(msg.id, { resolve, reject });
  ws.send(JSON.stringify(msg));
});

const evaluate = async (expression) => {
  const res = await send('Runtime.evaluate', {
    expression, awaitPromise: true, returnByValue: true,
  });
  if (res.exceptionDetails) throw new Error(res.exceptionDetails.text);
  return res.result.value;
};

const stats = () => evaluate(`(() => ({
  height: document.documentElement.scrollHeight,
  scrollY: Math.round(window.scrollY),
  sections: document.querySelectorAll('h2.month').length,
  grids: document.querySelectorAll('.grid').length,
  tiles: document.querySelectorAll('.tile[data-uuid]').length,
  skeletons: document.querySelectorAll('.tile.skeleton').length,
  monthErrors: document.querySelectorAll('.month-error').length,
  visibleSkeletons: [...document.querySelectorAll('.tile.skeleton')].filter((el) => {
    const r = el.getBoundingClientRect();
    return r.bottom > 0 && r.top < innerHeight;
  }).length,
  // what the eye actually judges: thumbnails decoded inside the viewport
  visibleImages: (() => {
    const imgs = [...document.querySelectorAll('.tile[data-uuid] img')].filter((el) => {
      const r = el.getBoundingClientRect();
      return r.bottom > 0 && r.top < innerHeight;
    });
    return { total: imgs.length, loaded: imgs.filter((i) => i.naturalWidth > 0).length };
  })(),
  lastMonth: document.querySelector('h2.month:last-of-type')?.textContent ?? null,
  lastFilled: (() => {
    const grids = [...document.querySelectorAll('.grid')];
    const last = grids[grids.length - 1];
    return last ? { month: last.dataset.month,
                    tiles: last.querySelectorAll('.tile[data-uuid]').length,
                    skeletons: last.querySelectorAll('.tile.skeleton').length } : null;
  })(),
  scrubber: (() => {
    const s = document.getElementById('scrubber');
    return { hidden: s.hidden, ticks: s.querySelectorAll('.tick').length,
             label: document.getElementById('scrubLabel').textContent,
             thumbTop: document.getElementById('scrubThumb').style.top };
  })(),
}))()`);

async function main() {
  await sleep(1200);
  const version = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
  const target = await (await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`,
                                    { method: 'PUT' })).json();
  console.log(version.Browser);

  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r) => { ws.onopen = r; });
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
      return;
    }
    if (msg.method === 'Runtime.exceptionThrown') {
      errors.push(msg.params.exceptionDetails.text + ' '
        + (msg.params.exceptionDetails.exception?.description ?? ''));
    }
    if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') {
      errors.push(msg.params.args.map((a) => a.value ?? a.description).join(' '));
    }
    if (msg.method === 'Network.loadingFailed') {
      failures.push(`${msg.params.type} ${msg.params.errorText}`);
    }
  };

  await send('Runtime.enable');
  await send('Network.enable');
  await send('Page.enable');
  await send('Network.setExtraHTTPHeaders', { headers: { Authorization: AUTH } });
  await send('Emulation.setDeviceMetricsOverride', {
    width: 412, height: 915, deviceScaleFactor: 2, mobile: true,
  });

  await send('Page.navigate', { url: URL_ + HASH });
  await sleep(4000);
  console.log('after load   ', JSON.stringify(await stats()));

  await evaluate('window.scrollTo(0, document.documentElement.scrollHeight)');
  await sleep(4000);
  console.log('after bottom ', JSON.stringify(await stats()));

  // Flick down in steps, the way a thumb scrolls
  for (const fraction of [0.25, 0.5, 0.75]) {
    await evaluate(
      `window.scrollTo(0, document.documentElement.scrollHeight * ${fraction})`);
    await sleep(2500);
    console.log(`at ${fraction * 100}%     `, JSON.stringify(await stats()));
  }

  // Drag the scrubber from top to bottom
  const box = await evaluate(`(() => {
    const r = document.getElementById('scrubber').getBoundingClientRect();
    return { x: Math.round(r.left + r.width / 2), top: Math.round(r.top),
             bottom: Math.round(r.bottom) };
  })()`);
  const drag = (type, y) => send('Input.dispatchMouseEvent', {
    type, x: box.x, y, button: 'left', buttons: 1, clickCount: 1,
    pointerType: 'touch',
  });
  await drag('mousePressed', box.top + 5);
  for (let i = 1; i <= 10; i += 1) {
    await drag('mouseMoved', box.top + ((box.bottom - box.top) * i) / 10);
    await sleep(120);
  }
  await drag('mouseReleased', box.bottom - 2);
  await sleep(4000);
  console.log('after drag   ', JSON.stringify(await stats()));

  if (errors.length) console.log('console errors:', errors.slice(0, 10));
  if (failures.length) console.log('failed requests:', [...new Set(failures)].slice(0, 10));
  if (!errors.length && !failures.length) console.log('no console errors, no failed requests');

  ws.close();
  chrome.kill();
}

main().catch(async (err) => {
  console.error('check failed:', err.message);
  chrome.kill();
  process.exit(1);
});
