// Offscreen document: owns the tab stream and the audio routing, and runs the Recorder.
// It can only use chrome.runtime from the extension APIs.
import { Recorder, DEFAULT_SERVER, logToServer } from './recorder-core.js';

const log = (stage, detail = '') => logToServer(DEFAULT_SERVER, 'offscreen', stage, detail);

let stream = null;
let playbackCtx = null;
let tabId = null;
let ended = false;   // true after the capture ended by itself, until the next start or ack

function releaseStream() {
  if (stream) stream.getTracks().forEach((t) => t.stop());
  if (playbackCtx) playbackCtx.close().catch(() => {});
  stream = null;
  playbackCtx = null;
}

const recorder = new Recorder({
  server: DEFAULT_SERVER,
  onEnded: () => {
    log('capture ended');
    const endedTab = tabId;
    releaseStream();
    tabId = null;
    ended = true;
    chrome.runtime.sendMessage({ target: 'background', type: 'ended', tabId: endedTab }).catch(() => {});
  },
});

async function start(msg) {
  if (recorder.state().recording) return { ok: false, error: 'already-recording' };
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: msg.streamId } },
      video: false,
    });
  } catch (err) {
    log('getUserMedia failed', err && err.name);
    return { ok: false, error: 'capture-failed' };
  }
  // Capturing a tab mutes it, so send the audio back to the speakers.
  playbackCtx = new AudioContext();
  playbackCtx.createMediaStreamSource(stream).connect(playbackCtx.destination);
  if (playbackCtx.resume) playbackCtx.resume().catch(() => {});

  try {
    await recorder.start(stream, msg.meta || {});
  } catch (err) {
    log('recorder start failed', err && err.message);
    releaseStream();
    return { ok: false, error: 'recorder-failed' };
  }
  tabId = msg.tabId ?? null;
  ended = false;
  log('recording started');
  return { ok: true };
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.target !== 'offscreen') return;
  (async () => {
    try {
      if (msg.type === 'start') {
        sendResponse(await start(msg));
      } else if (msg.type === 'stop') {
        const res = await recorder.stop(msg.reason || 'stopped');
        releaseStream();
        tabId = null;
        log('stopped', msg.reason || 'stopped');
        sendResponse({ ok: true, ...res });
      } else if (msg.type === 'state') {
        sendResponse({ ok: true, ...recorder.state(), tabId, ended });
      } else if (msg.type === 'ack') {
        ended = false;
        sendResponse({ ok: true });
      } else if (msg.type === 'nudge') {
        recorder.nudge();
        sendResponse({ ok: true });
      } else {
        sendResponse({ ok: false, error: 'unknown-message' });
      }
    } catch (err) {
      log('error', err && err.message);
      sendResponse({ ok: false, error: 'failed' });
    }
  })();
  return true;
});

recorder.resumePending().catch((err) => log('resume failed', err && err.message));
