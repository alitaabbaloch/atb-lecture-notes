// Popup: shows the recorder, the server control and the queue. It never runs code in a page.
import { DEFAULT_SERVER as SERVER } from './recorder-core.js';

const $ = (id) => document.getElementById(id);

const TEXT = {
  hostMissing: 'Start helper not installed. Run install-mac.command once.',
  older: 'An older server is running on this port. Close it, then press the server button.',
  buffering: 'Server is off. Audio is being kept in the browser and will upload when the server starts.',
  ended: 'The recording stopped by itself. Open the lesson tab and record again.',
  startFailed: 'The server did not start. Press the server button to try again.',
  busy: 'The server is busy with a recording or a transcription. Stop it when that has finished.',
  stopHelperFailed: 'The server could not be stopped. Close it from Activity Monitor or restart your Mac.',
  recordDenied: 'This tab cannot be recorded. Open the lesson page and try again.',
  recordFailed: 'Recording did not start. Reload the tab, then try again.',
  alreadyRecording: 'A recording is already running. Stop it first.',
  stopFailed: 'The recording could not be stopped. Close the lesson tab to stop it.',
  finishFailed: 'The lesson could not be finished. Check that the server is on.',
  retryFailed: 'The lesson could not be retried. Check that the server is on.',
  noExtension: 'The extension did not answer. Reload it from the extensions page.',
};

const SERVER_LABEL = {
  off: '[ server off ]',
  starting: '[ starting ]',
  on: '[ server on ]',
  confirm: '[ stop server? ]',
  older: '[ older server ]',
};

const WAVE_POINTS = 60;

let tab = null;
let rec = { recording: false };
let server = 'off';          // off | starting | on | older
let startingNow = false;
let hostMissing = false;
let jobs = [];
let jobsKey = '';
let flashNote = null;        // { text, kind, until }
let level = [];
let wasRecording = null;
let speeds = {};
let coursesLoaded = false;
let busy = false;
let ticks = 0;
let stopArmedUntil = 0;      // first click on server on arms the stop for 4 s
let endedSeen = false;      // the last capture ended by itself

// ---------- helpers ----------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function pad(n) { return String(n).padStart(2, '0'); }

function formatElapsed(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  return `${pad(Math.floor(total / 3600))}:${pad(Math.floor((total % 3600) / 60))}:${pad(total % 60)}`;
}

function flash(text, kind = 'bad', ms = 8000) {
  flashNote = { text, kind, until: Date.now() + ms };
}

async function send(message) {
  try { return await chrome.runtime.sendMessage(message); } catch { return null; }
}

async function api(path, options = {}) {
  return fetch(SERVER + path, { ...options, signal: AbortSignal.timeout(2500) });
}

function titleFromTab(title) {
  const text = (title || '').trim();
  // Remove a trailing site name such as " | Site" or " - Site".
  const cut = text.replace(/\s+[|–—\-·]\s+[^|–—\-·]*$/, '').trim();
  return cut || text;
}

// ---------- server ----------

async function checkServer() {
  try {
    const res = await api('/health');
    const body = await res.json().catch(() => null);
    if (!res.ok || !body || body.app !== 'atb-lecture-notes' || body.api !== 2) return 'older';
    return 'on';
  } catch {
    return 'off';
  }
}

async function refreshServer() {
  if (startingNow) return;
  server = await checkServer();
  if (server === 'off' && !hostMissing) {
    const res = await send({ target: 'background', type: 'server', cmd: 'status' });
    if (res && res.missing) hostMissing = true;
  } else if (server === 'on') {
    hostMissing = false;
  }
}

async function startServer() {
  if (startingNow) return;
  startingNow = true;
  server = 'starting';
  render();
  const res = await send({ target: 'background', type: 'server', cmd: 'start' });
  if (!res) {
    flash(TEXT.noExtension);
  } else if (res.missing) {
    hostMissing = true;
  } else if (!res.ok) {
    flash(TEXT.startFailed);
  }
  startingNow = false;
  server = await checkServer();
  if (server === 'on') hostMissing = false;
  else if (res && res.ok) flash(TEXT.startFailed);
  render();
}

async function stopServer() {
  const res = await send({ target: 'background', type: 'server', cmd: 'stop' });
  if (res && res.busy) flash(TEXT.busy, 'warn');
  else if (res && res.missing) hostMissing = true;
  else if (!res || !res.ok) flash(TEXT.stopHelperFailed);
  server = await checkServer();
}

async function refreshJobs() {
  try {
    const res = await api('/jobs?limit=8');
    if (!res.ok) return;
    jobs = await res.json();
  } catch { /* the server may have gone away; keep the old list */ }
}

async function loadCourses() {
  try {
    const res = await api('/courses');
    if (!res.ok) return;
    const list = await res.json();
    const box = $('courses');
    box.replaceChildren(...list.map((c) => {
      const opt = document.createElement('option');
      opt.value = c.name;
      return opt;
    }));
    coursesLoaded = true;
  } catch { /* try again on the next server check */ }
}

// ---------- recorder state ----------

async function refreshRec() {
  const res = await send({ target: 'background', type: 'state' });
  rec = res && res.ok ? res : { recording: false, noReply: true };
  if (rec.recording) {
    level.push(rec.level || 0);
    if (level.length > WAVE_POINTS) level.shift();
  } else {
    level = [];
  }
}

// ---------- rendering ----------

function drawWave() {
  const canvas = $('wave');
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height, mid = h / 2;
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = '#2A3B45';
  ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(0, mid + 0.5); ctx.lineTo(w, mid + 0.5); ctx.stroke();
  if (!level.length) return;
  ctx.strokeStyle = '#4FE3D3';
  ctx.beginPath();
  const step = w / (WAVE_POINTS - 1);
  level.forEach((v, i) => {
    const x = (WAVE_POINTS - level.length + i) * step;
    const amp = Math.max(0.5, v * (mid - 2));
    const y = mid + (i % 2 ? -amp : amp);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function segBar(fraction) {
  const bar = el('span', 'seg');
  const on = Math.round(Math.min(1, Math.max(0, fraction)) * 10);
  for (let i = 0; i < 10; i++) bar.append(el('i', i < on ? 'on' : ''));
  return bar;
}

function jobRow(job) {
  const li = el('li', `job ${job.status}`);
  li.append(el('div', 'job-title', job.lesson || 'Untitled lesson'));

  let fraction = null;
  let label = 'working';
  const hasProgress = typeof job.progress === 'number';
  switch (job.status) {
    case 'recording': label = 'recording'; break;
    case 'queued': fraction = 0; label = 'queued'; break;
    case 'transcribing':
      fraction = hasProgress ? job.progress : 0;
      label = hasProgress
        ? `transcribing ${job.progress_estimated ? '~' : ''}${Math.round(job.progress * 100)}%`
        : 'transcribing';
      break;
    case 'summarizing': fraction = 1; label = 'writing notes'; break;
    case 'done': fraction = 1; label = 'done'; break;
    case 'error': label = 'failed'; break;
    default: break;
  }
  const meta = el('div', 'job-meta');
  if (fraction !== null) meta.append(segBar(fraction));
  meta.append(el('span', 'job-state', job.recovered ? `${label} (recovered)` : label));
  li.append(meta);

  if (job.status === 'done') {
    if (job.output) li.append(el('div', 'job-path', job.output));
    if (job.notes_error) li.append(el('div', 'job-path', 'Notes were skipped for this lesson.'));
  }
  if (job.status === 'recording' && job.stalled === true && job.sid && !rec.recording && !(rec.pendingChunks > 0)) {
    li.append(el('div', 'job-err', 'No audio is arriving for this lesson.'));
    const fin = el('button', 'retry', 'Finish now');
    fin.type = 'button';
    fin.addEventListener('click', async () => {
      fin.disabled = true;
      try {
        const res = await api(`/sessions/${encodeURIComponent(job.sid)}/finish`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ reason: 'finished by hand' }),
        });
        if (!res.ok) flash(TEXT.finishFailed);
      } catch { flash(TEXT.finishFailed); }
      await refreshJobs();
      render();
    });
    li.append(fin);
  }
  if (job.status === 'error') {
    li.append(el('div', 'job-err', 'Transcription failed. Press Retry to try again.'));
    const retry = el('button', 'retry', 'Retry');
    retry.type = 'button';
    retry.addEventListener('click', async () => {
      retry.disabled = true;
      try {
        const res = await api(`/jobs/${encodeURIComponent(job.id)}/retry`, { method: 'POST' });
        if (!res.ok) flash(TEXT.retryFailed);
      } catch { flash(TEXT.retryFailed); }
      await refreshJobs();
      render();
    });
    li.append(retry);
  }
  return li;
}

function renderQueue() {
  const key = JSON.stringify(jobs.map((j) => [j.id, j.status, j.progress, j.output, j.recovered, j.notes_error, j.stalled, j.sid, !rec.recording && !(rec.pendingChunks > 0)]));
  $('queue-title').textContent = `queue ${pad(jobs.length)}`;
  $('queue-empty').hidden = jobs.length > 0;
  $('queue-empty').textContent = server === 'on' ? 'no lessons yet' : 'the queue shows when the server is on';
  if (key === jobsKey) return;
  jobsKey = key;
  $('queue').replaceChildren(...jobs.map(jobRow));
}

function noteList() {
  const notes = [];
  if (flashNote && flashNote.until > Date.now()) notes.push([flashNote.text, flashNote.kind]);
  if (hostMissing) notes.push([TEXT.hostMissing, 'bad']);
  if (server === 'older') notes.push([TEXT.older, 'bad']);
  if (rec.recording && (server === 'off' || server === 'starting')) notes.push([TEXT.buffering, 'warn']);
  if (rec.pendingChunks > 0) {
    const mb = ((rec.pendingBytes || 0) / 1048576).toFixed(1);
    const secs = rec.pendingChunks * 5;
    const time = secs >= 60 ? `${Math.floor(secs / 60)} min ${secs % 60} s` : `${secs} s`;
    notes.push([`${time} of audio waiting to upload (${mb} MB)`, 'info']);
  }
  if (rec.recording && rec.silentMs > 20000) {
    notes.push([`No sound from this tab for ${Math.floor(rec.silentMs / 1000)} s`, 'warn']);
  }
  if (endedSeen && !rec.recording) notes.push([TEXT.ended, 'bad']);
  return notes;
}

function render() {
  const srv = $('server');
  const armed = server === 'on' && Date.now() < stopArmedUntil;
  const shown = armed ? 'confirm' : server;
  srv.textContent = SERVER_LABEL[shown];
  srv.className = `srv ${armed ? 'starting' : server}`;

  const recording = !!rec.recording;
  if (recording !== wasRecording) {
    wasRecording = recording;
    if (recording) {
      $('course').value = rec.course || '';
      $('lesson').value = rec.lesson || '';
      $('speed').value = String(rec.speed || 1);
    }
    for (const id of ['course', 'lesson', 'speed']) $(id).disabled = recording;
  }
  $('idle').hidden = recording;
  $('live').hidden = !recording;
  if (recording) {
    $('timer').textContent = formatElapsed(Date.now() - rec.startedAt);
    $('live-speed').textContent = `speed ${rec.speed || 1}x`;
    drawWave();
  }

  const notes = $('notes');
  notes.replaceChildren(...noteList().map(([text, kind]) => el('p', `note ${kind}`, text)));
  renderQueue();
}

// ---------- actions ----------

async function onRecord() {
  if (busy || !tab) return;
  busy = true;
  $('record').disabled = true;
  const course = $('course').value.trim() || 'Course';
  const meta = {
    course,
    lesson: $('lesson').value.trim() || 'Lesson',
    url: tab.url || '',
    speed: Number($('speed').value) || 1,
  };
  endedSeen = false;
  speeds[course] = $('speed').value;
  chrome.storage.local.set({ course, speeds });

  const res = await send({ target: 'background', type: 'start', tabId: tab.id, meta });
  if (!res) flash(TEXT.noExtension);
  else if (!res.ok) {
    flash(res.error === 'capture-denied' ? TEXT.recordDenied
      : res.error === 'already-recording' ? TEXT.alreadyRecording
      : TEXT.recordFailed);
  } else if (server !== 'on') {
    startServer();   // the audio is already being kept; try to bring the server up at the same time
  }
  await refreshRec();
  $('record').disabled = false;
  busy = false;
  render();
}

async function onStop() {
  if (busy) return;
  busy = true;
  $('stop').disabled = true;
  const res = await send({ target: 'background', type: 'stop' });
  if (!res || !res.ok) flash(TEXT.stopFailed);
  await refreshRec();
  await refreshJobs();
  $('stop').disabled = false;
  busy = false;
  render();
}

async function onServerButton() {
  if (server === 'on') {
    if (Date.now() < stopArmedUntil) { stopArmedUntil = 0; await stopServer(); }
    else stopArmedUntil = Date.now() + 4000;
  }
  else if (server === 'off' || server === 'older') await startServer();
  render();
}

function onCourseChange() {
  const saved = speeds[$('course').value.trim()];
  if (saved) $('speed').value = saved;
}

// ---------- loop ----------

async function tick() {
  ticks += 1;
  await refreshRec();
  if (rec.ended) {
    endedSeen = true;
    send({ target: 'background', type: 'ack' });
  }
  if (ticks % 12 === 1) {
    await refreshServer();
    if (server === 'on') {
      await refreshJobs();
      if (!coursesLoaded) await loadCourses();
    }
  }
  render();
  setTimeout(tick, 250);
}

async function init() {
  $('record').addEventListener('click', onRecord);
  $('stop').addEventListener('click', onStop);
  $('server').addEventListener('click', onServerButton);
  $('course').addEventListener('change', onCourseChange);

  const stored = await chrome.storage.local.get(['course', 'speeds']);
  speeds = stored.speeds || {};
  $('course').value = stored.course || '';
  onCourseChange();

  try {
    [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  } catch { tab = null; }
  $('lesson').value = titleFromTab(tab && tab.title);

  tick();
}

init();
