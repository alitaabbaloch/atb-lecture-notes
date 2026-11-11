// Recorder core: records a MediaStream, keeps every chunk in IndexedDB, then uploads
// the chunks one at a time to the local server. No extension APIs are used here, so a
// plain web page (tools/testpage/harness.html) can import and test this file.

export const DEFAULT_SERVER = 'http://127.0.0.1:8765';

const BACKOFF_MS = [1000, 2000, 5000, 10000, 30000];
const CHUNK_MS = 5000;
const MIME = 'audio/webm;codecs=opus';
const BITS_PER_SECOND = 48000;
const SILENCE_RMS = 0.003;

export function logToServer(server, src, stage, detail = '') {
  try {
    fetch(`${server}/log`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ src, stage, detail: String(detail) }),
    }).catch(() => {});
  } catch { /* logging must never break recording */ }
}

class HttpError extends Error {
  constructor(status) { super(`http ${status}`); this.status = status; }
}

// ---------- IndexedDB helpers ----------

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open('atb', 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      db.createObjectStore('sessions', { keyPath: 'clientId' });
      db.createObjectStore('chunks', { keyPath: ['clientId', 'seq'] });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function wrap(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function chunkRange(clientId) {
  return IDBKeyRange.bound([clientId, 0], [clientId, Number.MAX_SAFE_INTEGER]);
}

// ---------- Recorder ----------

export class Recorder {
  constructor({ server = DEFAULT_SERVER, onEnded = null } = {}) {
    this.server = server;
    this.onEnded = onEnded;           // called after the capture ended by itself
    this._dbPromise = null;
    this._active = null;              // the recording in progress, if any
    this._writeChain = Promise.resolve();
    this._busy = new Set();           // clientIds with an upload loop running
    this._again = new Set();          // clientIds that got new work while busy
    this._wakers = new Set();         // functions that cut a backoff sleep short
    this._pending = { chunks: 0, bytes: 0 };
    this._reachable = true;
    this._lastError = null;
    this._level = 0;
    this._silentSince = null;
    this._levelTimer = null;
  }

  setServer(server) { this.server = server; }

  _db() { return (this._dbPromise ||= openDb()); }

  async _get(store, key) {
    const db = await this._db();
    return wrap(db.transaction(store).objectStore(store).get(key));
  }

  async _put(store, value) {
    const db = await this._db();
    const tx = db.transaction(store, 'readwrite');
    tx.objectStore(store).put(value);
    await new Promise((res, rej) => { tx.oncomplete = res; tx.onerror = tx.onabort = () => rej(tx.error); });
  }

  async _delete(store, key) {
    const db = await this._db();
    const tx = db.transaction(store, 'readwrite');
    tx.objectStore(store).delete(key);
    await new Promise((res, rej) => { tx.oncomplete = res; tx.onerror = tx.onabort = () => rej(tx.error); });
  }

  // Read, change and write one session inside a single transaction.
  async _update(clientId, change) {
    const db = await this._db();
    return new Promise((resolve, reject) => {
      const tx = db.transaction('sessions', 'readwrite');
      const store = tx.objectStore('sessions');
      const get = store.get(clientId);
      let result = null;
      get.onsuccess = () => {
        if (!get.result) return;
        result = get.result;
        change(result);
        store.put(result);
      };
      tx.oncomplete = () => resolve(result);
      tx.onerror = tx.onabort = () => reject(tx.error);
    });
  }

  async _firstChunk(clientId) {
    const db = await this._db();
    return new Promise((resolve, reject) => {
      const req = db.transaction('chunks').objectStore('chunks').openCursor(chunkRange(clientId));
      req.onsuccess = () => resolve(req.result ? req.result.value : null);
      req.onerror = () => reject(req.error);
    });
  }

  async _deleteChunk(chunk) {
    await this._delete('chunks', [chunk.clientId, chunk.seq]);
    this._lastError = null;   // the server answered and took the data
    this._pending.chunks = Math.max(0, this._pending.chunks - 1);
    this._pending.bytes = Math.max(0, this._pending.bytes - chunk.size);
  }

  async _dropChunks(clientId) {
    const db = await this._db();
    const tx = db.transaction('chunks', 'readwrite');
    tx.objectStore('chunks').delete(chunkRange(clientId));
    await new Promise((res, rej) => { tx.oncomplete = res; tx.onerror = tx.onabort = () => rej(tx.error); });
    await this._refreshPending();
  }

  async _refreshPending() {
    const db = await this._db();
    const totals = await new Promise((resolve, reject) => {
      let chunks = 0, bytes = 0;
      const req = db.transaction('chunks').objectStore('chunks').openCursor();
      req.onsuccess = () => {
        const cursor = req.result;
        if (!cursor) return resolve({ chunks, bytes });
        chunks += 1; bytes += cursor.value.size;
        cursor.continue();
      };
      req.onerror = () => reject(req.error);
    });
    this._pending = totals;
  }

  // ---------- public API ----------

  async start(stream, meta = {}) {
    if (this._active) throw new Error('already recording');
    const track = stream.getAudioTracks()[0];
    if (!track) throw new Error('no audio track');

    const clientId = crypto.randomUUID();
    const recorder = new MediaRecorder(stream, { mimeType: MIME, audioBitsPerSecond: BITS_PER_SECOND });
    const session = {
      clientId,
      sid: null,
      course: meta.course || '',
      lesson: meta.lesson || '',
      url: meta.url || '',
      speed: Number(meta.speed) || 1,
      startedAt: new Date().toISOString(),
      state: 'recording',
      nextSeq: 0,
      offset: 0,
      finishRequested: false,
      reason: null,
    };
    await this._put('sessions', session);

    const active = { clientId, recorder, track, meta: session, ctx: null, analyser: null, buffer: null };
    this._active = active;
    this._lastError = null;

    recorder.ondataavailable = (event) => {
      if (!event.data || !event.data.size) return;
      this._writeChain = this._writeChain
        .then(() => this._addChunk(clientId, event.data))
        .catch(() => { this._lastError = 'could not store audio in the browser'; });
    };
    recorder.onerror = () => { this._lastError = 'recorder error'; };

    // Level meter and silence detection. Separate from any playback the caller does.
    try {
      const ctx = new AudioContext();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      ctx.createMediaStreamSource(stream).connect(analyser);
      if (ctx.resume) ctx.resume().catch(() => {});
      active.ctx = ctx;
      active.analyser = analyser;
      active.buffer = new Float32Array(analyser.fftSize);
    } catch { /* meter is optional */ }
    this._silentSince = active.analyser ? Date.now() : null;
    this._levelTimer = setInterval(() => this._sampleLevel(active), 100);

    track.addEventListener('ended', () => {
      if (this._active === active) this._captureEnded();
    });

    recorder.start(CHUNK_MS);
    this._kick(clientId);   // creates the session on the server as soon as it can
    return { clientId };
  }

  async stop(reason = 'stopped') {
    const active = this._active;
    if (!active) return { ok: true, already: true };
    this._active = null;
    clearInterval(this._levelTimer);
    this._levelTimer = null;
    this._level = 0;
    this._silentSince = null;

    await new Promise((resolve) => {
      if (active.recorder.state === 'inactive') return resolve();
      active.recorder.onstop = () => resolve();
      try { active.recorder.stop(); } catch { resolve(); }
    });
    await this._writeChain;
    if (active.ctx) active.ctx.close().catch(() => {});

    await this._update(active.clientId, (s) => {
      s.state = 'stopped';
      s.finishRequested = true;
      s.reason = reason;
    });
    this._kick(active.clientId);
    return { ok: true, clientId: active.clientId };
  }

  state() {
    const active = this._active;
    const meta = active ? active.meta : null;
    return {
      recording: !!active,
      clientId: active ? active.clientId : null,
      course: meta ? meta.course : null,
      lesson: meta ? meta.lesson : null,
      speed: meta ? meta.speed : null,
      startedAt: meta ? Date.parse(meta.startedAt) : null,
      level: this._level,
      silentMs: active && this._silentSince != null ? Date.now() - this._silentSince : 0,
      pendingChunks: this._pending.chunks,
      pendingBytes: this._pending.bytes,
      serverReachable: this._reachable,
      lastError: this._lastError,
    };
  }

  // Finish every session a crash left behind: upload what is stored, then finish.
  async resumePending() {
    const db = await this._db();
    const all = await wrap(db.transaction('sessions').objectStore('sessions').getAll());
    for (const s of all) {
      if (this._active && this._active.clientId === s.clientId) continue;
      await this._update(s.clientId, (x) => {
        if (!x.finishRequested) { x.finishRequested = true; x.state = 'stopped'; x.reason = 'recovered'; }
      });
      this._kick(s.clientId);
    }
    await this._refreshPending();
    return all.length;
  }

  // Retry uploads right now instead of waiting for the backoff timer.
  nudge() {
    for (const wake of [...this._wakers]) wake();
  }

  // ---------- internals ----------

  _sampleLevel(active) {
    if (!active.analyser) return;
    active.analyser.getFloatTimeDomainData(active.buffer);
    let sum = 0;
    for (let i = 0; i < active.buffer.length; i++) sum += active.buffer[i] * active.buffer[i];
    const rms = Math.sqrt(sum / active.buffer.length);
    this._level = Math.min(1, rms * 5);
    if (rms < SILENCE_RMS) { if (this._silentSince == null) this._silentSince = Date.now(); }
    else this._silentSince = null;
  }

  async _captureEnded() {
    await this.stop('capture ended');
    try { if (this.onEnded) this.onEnded(); } catch { /* ignore */ }
  }

  async _addChunk(clientId, blob) {
    const db = await this._db();
    let added = false;
    await new Promise((resolve, reject) => {
      const tx = db.transaction(['sessions', 'chunks'], 'readwrite');
      const sessions = tx.objectStore('sessions');
      const get = sessions.get(clientId);
      get.onsuccess = () => {
        const s = get.result;
        if (!s) return;
        tx.objectStore('chunks').put({ clientId, seq: s.nextSeq, offset: s.offset, size: blob.size, blob });
        s.nextSeq += 1;
        s.offset += blob.size;
        sessions.put(s);
        added = true;
      };
      tx.oncomplete = resolve;
      tx.onerror = tx.onabort = () => reject(tx.error);
    });
    if (added) {
      this._pending.chunks += 1;
      this._pending.bytes += blob.size;
      this._kick(clientId);
    }
  }

  _kick(clientId) {
    if (this._busy.has(clientId)) { this._again.add(clientId); return; }
    this._busy.add(clientId);
    this._drain(clientId).catch(() => { this._lastError = 'internal error'; });
  }

  _sleep(ms) {
    return new Promise((resolve) => {
      const done = () => { clearTimeout(timer); this._wakers.delete(done); resolve(); };
      const timer = setTimeout(done, ms);
      this._wakers.add(done);
    });
  }

  async _http(method, path, { json, headers = {}, body } = {}) {
    const h = { ...headers };
    if (json !== undefined) { h['Content-Type'] = 'application/json'; body = JSON.stringify(json); }
    try {
      const res = await fetch(this.server + path, { method, headers: h, body, signal: AbortSignal.timeout(30000) });
      this._reachable = true;
      return res;
    } catch (err) {
      this._reachable = false;
      this._lastError = 'server unreachable';
      throw err;
    }
  }

  // One upload loop per session: create the session, send chunks in order, then finish.
  async _drain(clientId) {
    let attempt = 0;
    try {
      for (;;) {
        try {
          let s = await this._get('sessions', clientId);
          if (!s) return;

          if (!s.sid) {
            const res = await this._http('POST', '/sessions', {
              json: {
                client_session_id: clientId, course: s.course, lesson: s.lesson,
                url: s.url, speed: s.speed, started_at: s.startedAt,
              },
            });
            if (!res.ok) throw new HttpError(res.status);
            const body = await res.json();
            s = await this._update(clientId, (x) => { x.sid = body.sid; });
            if (!s) return;
          }

          const chunk = await this._firstChunk(clientId);
          if (chunk) {
            await this._sendChunk(s, chunk);
            attempt = 0;
            continue;
          }

          if (s.finishRequested) {
            const res = await this._http('POST', `/sessions/${s.sid}/finish`, {
              json: { reason: s.reason || 'stopped', ended_at: new Date().toISOString(), total_bytes: s.offset },
            });
            if (res.status === 404) {
              await this._update(clientId, (x) => { x.sid = null; });   // server forgot it; create again
              throw new HttpError(404);
            }
            if (!res.ok) throw new HttpError(res.status);
            await this._delete('sessions', clientId);
            this._lastError = null;
            return;
          }

          if (this._again.delete(clientId)) continue;
          return;
        } catch (err) {
          this._lastError = err instanceof HttpError ? `server answered ${err.status}` : 'server unreachable';
          const wait = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
          attempt += 1;
          await this._sleep(wait);
        }
      }
    } finally {
      this._busy.delete(clientId);
    }
  }

  async _sendChunk(s, chunk) {
    const path = `/sessions/${s.sid}/chunks/${chunk.seq}`;
    const res = await this._http('PUT', path, { headers: { 'X-Offset': String(chunk.offset) }, body: chunk.blob });
    if (res.status === 200) { await this._deleteChunk(chunk); return; }

    if (res.status === 404) {
      await this._update(s.clientId, (x) => { x.sid = null; });
      throw new HttpError(404);
    }

    if (res.status === 409) {
      const info = await res.json().catch(() => ({}));
      if (typeof info.received_bytes === 'number') {
        const have = info.received_bytes;
        if (have >= chunk.offset + chunk.size) { await this._deleteChunk(chunk); return; }
        if (have > chunk.offset) {
          // Server has the first part of this chunk: send the rest.
          const rest = await this._http('PUT', path, {
            headers: { 'X-Offset': String(have) },
            body: chunk.blob.slice(have - chunk.offset),
          });
          if (rest.status === 200) { await this._deleteChunk(chunk); return; }
          throw new HttpError(rest.status);
        }
        // The server is missing bytes we already deleted. Nothing more can be sent.
        await this._abandon(s.clientId, 'upload gap');
        return;
      }
      // Session is no longer open on the server (state answer): finish it with what it has.
      await this._abandon(s.clientId, 'session closed');
      return;
    }
    throw new HttpError(res.status);
  }

  async _abandon(clientId, reason) {
    await this._dropChunks(clientId);
    await this._update(clientId, (x) => { x.finishRequested = true; x.state = 'stopped'; x.reason = reason; });
    this._lastError = reason;
  }
}
