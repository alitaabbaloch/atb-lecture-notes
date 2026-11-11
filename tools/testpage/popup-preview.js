// Runs the real popup.js in a normal tab against a fake chrome object and a fake server.
// The state comes from the query string: ?state=off|idle|recording|buffering|error
// plus a few extras: starting, nohost, older, silent, ended.
import { DEFAULT_SERVER as SERVER } from '/dev/ext/recorder-core.js';

const mode = new URLSearchParams(location.search).get('state') || 'idle';
const STATES = ['off', 'idle', 'recording', 'buffering', 'error', 'starting', 'nohost', 'older', 'silent', 'ended', 'stalled'];
document.getElementById('states').replaceChildren(...STATES.map((s) => {
  const a = document.createElement('a');
  a.href = `?state=${s}`;
  a.textContent = s;
  return a;
}));

const fake = {
  serverOn: ['idle', 'recording', 'error', 'silent', 'ended', 'stalled'].includes(mode),
  older: mode === 'older',
  hostMissing: mode === 'nohost',
  recording: ['recording', 'buffering', 'silent'].includes(mode),
  startedAt: Date.now() - (12 * 60 + 48) * 1000,
  speed: 1.5,
  pendingChunks: mode === 'buffering' ? 14 : 0,
  silentMs: mode === 'silent' ? 25000 : 0,
  ended: mode === 'ended',
};

const jobsFor = () => {
  if (!fake.serverOn) return [];
  if (['recording', 'silent'].includes(mode)) {
    return [
      { id: 'j2', lesson: 'Content coverage and grouping', status: 'transcribing', progress: 0.64, progress_estimated: true },
      { id: 'j1', lesson: 'Keyword research basics', status: 'done', progress: 1, output: 'notes/sample-course/01-keyword-research-basics.md' },
    ];
  }
  if (mode === 'stalled') {
    return [{ id: 'j4', sid: 's4', lesson: 'Link building basics', status: 'recording', progress: null, stalled: true }];
  }
  if (mode === 'error') {
    return [{ id: 'j3', lesson: 'Page structure and links', status: 'error', progress: null, error: 'boom' }];
  }
  return [];
};
let jobs = jobsFor();

// ---- fake server (only in this preview) ----
const realFetch = window.fetch.bind(window);
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
window.fetch = async (input, init = {}) => {
  const url = typeof input === 'string' ? input : input.url;
  if (!url.startsWith(SERVER)) return realFetch(input, init);
  const path = url.slice(SERVER.length);
  if (!fake.serverOn && !fake.older) throw new TypeError('Failed to fetch');
  if (path === '/health') {
    if (fake.older) return json({ ok: true, app: 'something-else' });
    return json({ ok: true, app: 'atb-lecture-notes', version: '1.0.0', api: 2, queue: { recording: 0, queued: 0, running: 0 } });
  }
  if (path === '/courses') return json([{ id: 1, name: 'Sample course', slug: 'sample-course', lessons: 4 }, { id: 2, name: 'Another course', slug: 'another-course', lessons: 0 }]);
  if (path.startsWith('/jobs?')) return json(jobs);
  const retry = path.match(/^\/jobs\/([^/]+)\/retry$/);
  const fin = path.match(/^\/sessions\/([^/]+)\/finish$/);
  if (fin && init.method === 'POST') {
    jobs = jobs.map((j) => (j.sid === fin[1] ? { ...j, status: 'queued', stalled: false } : j));
    return json({ job_id: 'x', lesson_id: 1, empty: false });
  }
  if (retry && init.method === 'POST') {
    jobs = jobs.map((j) => (j.id === retry[1] ? { ...j, status: 'queued', progress: null } : j));
    return json(jobs[0]);
  }
  return json({ status: 'unknown' }, 404);
};

// ---- fake chrome ----
const store = {};
const level = () => Math.min(1, Math.max(0.02, 0.35 + 0.3 * Math.sin(Date.now() / 300) + 0.15 * (Math.random() - 0.5)));

async function background(msg) {
  switch (msg.type) {
    case 'state':
      return {
        ok: true,
        recording: fake.recording,
        course: 'Sample course',
        lesson: 'Content coverage and grouping',
        speed: fake.speed,
        startedAt: fake.startedAt,
        level: fake.recording && !fake.silentMs ? level() : 0.01,
        silentMs: fake.silentMs,
        pendingChunks: fake.pendingChunks,
        pendingBytes: fake.pendingChunks * 300000,
        serverReachable: fake.serverOn,
        lastError: null,
        tabId: 1,
        ended: fake.ended,
      };
    case 'start':
      fake.recording = true;
      fake.startedAt = Date.now();
      fake.speed = msg.meta.speed;
      return { ok: true };
    case 'stop':
      fake.recording = false;
      return { ok: true };
    case 'ack':
      fake.ended = false;
      return { ok: true };
    case 'server':
      if (fake.hostMissing) return { ok: false, missing: true, error: 'host-failed' };
      if (msg.cmd === 'status') return { ok: true, running: fake.serverOn };
      if (msg.cmd === 'start') {
        await new Promise((r) => setTimeout(r, mode === 'starting' ? 60000 : 1500));
        fake.serverOn = true;
        return { ok: true, running: true, started: true };
      }
      if (msg.cmd === 'stop') { fake.serverOn = false; return { ok: true, stopped: true }; }
      return { ok: true };
    default:
      return { ok: false };
  }
}

window.chrome = {
  runtime: { sendMessage: async (msg) => (msg && msg.target === 'background' ? background(msg) : undefined) },
  storage: {
    local: {
      get: async (keys) => Object.fromEntries([].concat(keys).filter((k) => k in store).map((k) => [k, store[k]])),
      set: async (values) => { Object.assign(store, values); },
    },
  },
  tabs: {
    query: async () => [{ id: 1, title: 'Content coverage and grouping - Example Academy', url: 'https://example.com/lesson/3' }],
  },
};

// ---- load the real popup markup, then the real popup.js ----
const html = await (await realFetch('/dev/ext/popup.html')).text();
const doc = new DOMParser().parseFromString(html, 'text/html');
doc.querySelectorAll('script').forEach((s) => s.remove());
document.getElementById('popup-root').innerHTML = doc.body.innerHTML;

await import('/dev/ext/popup.js');
if (mode === 'starting') document.getElementById('server').click();
