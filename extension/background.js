// Service worker: a stateless router. The offscreen document knows what is recording.
import { DEFAULT_SERVER, logToServer } from './recorder-core.js';

const HOST = 'com.atb.lecture_notes';
const SERVER_COMMANDS = ['status', 'start', 'stop', 'open_library'];
const log = (stage, detail = '') => logToServer(DEFAULT_SERVER, 'background', stage, detail);

// ---------- badge ----------

function setBadge(text, color) {
  chrome.action.setBadgeText({ text });
  if (text) chrome.action.setBadgeBackgroundColor({ color });
}
const badgeRecording = () => setBadge('REC', '#F56105');
const badgeEnded = () => setBadge('!', '#E24B4A');
const badgeClear = () => setBadge('', '#000000');

// ---------- offscreen document ----------

let creating = null;   // only avoids two creates at once; can be rebuilt any time

async function hasOffscreen() {
  if (chrome.runtime.getContexts) {
    const contexts = await chrome.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'] });
    return contexts.length > 0;
  }
  return chrome.offscreen.hasDocument();
}

async function ensureOffscreen() {
  if (await hasOffscreen()) return;
  if (!creating) {
    creating = chrome.offscreen.createDocument({
      url: 'offscreen.html',
      reasons: ['USER_MEDIA'],
      justification: 'Record tab audio and upload it to the local transcription server.',
    }).finally(() => { creating = null; });
  }
  await creating;
}

// The new document may not be listening yet, so retry a few times.
async function sendOffscreen(message) {
  let lastError = null;
  for (let i = 0; i < 8; i++) {
    try {
      return await chrome.runtime.sendMessage({ target: 'offscreen', ...message });
    } catch (err) {
      lastError = err;
      await new Promise((r) => setTimeout(r, 150));
    }
  }
  throw lastError;
}

async function askOffscreen(message) {
  if (!(await hasOffscreen())) return null;
  try { return await sendOffscreen(message); } catch { return null; }
}

// ---------- native messaging ----------

function native(cmd) {
  return new Promise((resolve) => {
    chrome.runtime.sendNativeMessage(HOST, { cmd }, (reply) => {
      const err = chrome.runtime.lastError;
      if (err) {
        const missing = /not found|forbidden|not allowed/i.test(String(err.message || ''));
        resolve({ ok: false, missing, error: 'host-failed' });
        return;
      }
      if (!reply || typeof reply !== 'object') { resolve({ ok: false, error: 'host-failed' }); return; }
      resolve({
        ok: !!reply.ok,
        running: reply.running,
        started: reply.started,
        stopped: reply.stopped,
        busy: reply.busy,
        version: reply.version,
        error: reply.ok || reply.busy ? undefined : 'host-failed',
      });
    });
  });
}

// ---------- commands ----------

async function startRecording(msg) {
  const current = await askOffscreen({ type: 'state' });
  if (current && current.recording) return { ok: false, error: 'already-recording' };

  let streamId;
  try {
    streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: msg.tabId });
  } catch (err) {
    log('getMediaStreamId failed');
    return { ok: false, error: 'capture-denied' };
  }
  await ensureOffscreen();
  const res = await sendOffscreen({ type: 'start', streamId, meta: msg.meta, tabId: msg.tabId });
  if (!res || !res.ok) return { ok: false, error: (res && res.error) || 'failed' };
  badgeRecording();
  return { ok: true };
}

async function stopRecording(reason) {
  const res = await askOffscreen({ type: 'stop', reason });
  badgeClear();
  return res || { ok: true, already: true };
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.target !== 'background') return;
  (async () => {
    try {
      switch (msg.type) {
        case 'start':
          sendResponse(await startRecording(msg));
          break;
        case 'stop':
          sendResponse(await stopRecording('stopped'));
          break;
        case 'state': {
          const st = await askOffscreen({ type: 'state' });
          sendResponse(st || { ok: true, recording: false });
          break;
        }
        case 'ack':
          await askOffscreen({ type: 'ack' });
          badgeClear();
          sendResponse({ ok: true });
          break;
        case 'server': {
          if (!SERVER_COMMANDS.includes(msg.cmd)) { sendResponse({ ok: false, error: 'unknown-command' }); break; }
          const res = await native(msg.cmd);
          if (msg.cmd === 'start' && res.ok) askOffscreen({ type: 'nudge' });
          sendResponse(res);
          break;
        }
        case 'ended':
          // The capture ended by itself. If the tab is gone, that is just a closed tab.
          chrome.tabs.get(msg.tabId).then(badgeEnded, badgeClear);
          sendResponse({ ok: true });
          break;
        default:
          sendResponse({ ok: false, error: 'unknown-message' });
      }
    } catch (err) {
      log('error', err && err.message);
      sendResponse({ ok: false, error: 'failed' });
    }
  })();
  return true;
});

// If the recorded tab is closed, stop and upload what we have.
chrome.tabs.onRemoved.addListener(async (tabId) => {
  const st = await askOffscreen({ type: 'state' });
  if (st && st.recording && st.tabId === tabId) await stopRecording('tab closed');
});

// Keep an offscreen document around so audio left in the browser resumes uploading.
const openOffscreen = () => ensureOffscreen().catch(() => {});
chrome.runtime.onStartup.addListener(openOffscreen);
chrome.runtime.onInstalled.addListener(openOffscreen);
