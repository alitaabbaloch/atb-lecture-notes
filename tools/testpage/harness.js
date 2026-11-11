// Drives Recorder with a synthetic beep stream, so no tab capture is needed.
import { Recorder } from '/dev/ext/recorder-core.js';

const serverInput = document.getElementById('server');
serverInput.value = location.origin;

const recorder = new Recorder({ server: serverInput.value });
window.recorder = recorder;   // handy for an automated browser

let ctx = null, osc = null, gain = null, beepTimer = null, stream = null, beeps = 0, count = 0;

function beep() {
  const t = ctx.currentTime;
  osc.frequency.setValueAtTime(beeps % 4 === 3 ? 880 : 440, t);
  gain.gain.cancelScheduledValues(t);
  gain.gain.setValueAtTime(0.0001, t);
  gain.gain.exponentialRampToValueAtTime(0.4, t + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.25);
  beeps += 1;
}

function buildStream() {
  ctx = new AudioContext();
  gain = ctx.createGain();
  gain.gain.value = 0.0001;
  osc = ctx.createOscillator();
  osc.connect(gain);
  const dest = ctx.createMediaStreamDestination();
  gain.connect(dest);
  osc.start();
  beep();
  beepTimer = setInterval(beep, 1000);
  return dest.stream;
}

function teardown() {
  clearInterval(beepTimer);
  beepTimer = null;
  if (stream) stream.getTracks().forEach((t) => t.stop());
  if (ctx) ctx.close();
  ctx = osc = gain = stream = null;
}

document.getElementById('start').addEventListener('click', async () => {
  if (recorder.state().recording) return;
  recorder.setServer(serverInput.value.replace(/\/+$/, ''));
  await ctx?.close?.();
  stream = buildStream();
  count += 1;
  await recorder.start(stream, {
    course: 'Harness course',
    lesson: `Harness lesson ${count}`,
    url: location.href,
    speed: 1,
  });
});

document.getElementById('stop').addEventListener('click', async () => {
  await recorder.stop('stopped');
  teardown();
});

document.getElementById('resume').addEventListener('click', () => {
  recorder.setServer(serverInput.value.replace(/\/+$/, ''));
  recorder.resumePending();
});

const out = document.getElementById('state');
setInterval(() => { out.textContent = JSON.stringify(recorder.state()); }, 500);
out.textContent = JSON.stringify(recorder.state());
