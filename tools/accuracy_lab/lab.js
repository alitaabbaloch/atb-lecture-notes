// The listening test page: one clip per screen, one click per answer. No libraries.

// ---- word diff (pure functions, also loaded by a Node test) ----
function labWords(text) { return String(text || "").split(/\s+/).filter(Boolean); }

// Longest common subsequence of two word lists: returns {index in a: index in b}.
function labLcs(a, b) {
  var n = a.length, m = b.length, t = [], i, j;
  for (i = 0; i <= n; i++) { t.push(new Array(m + 1).fill(0)); }
  for (i = n - 1; i >= 0; i--) for (j = m - 1; j >= 0; j--)
    t[i][j] = a[i] === b[j] ? t[i + 1][j + 1] + 1 : Math.max(t[i + 1][j], t[i][j + 1]);
  var map = {};
  i = 0; j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { map[i] = j; i++; j++; }
    else if (t[i + 1][j] >= t[i][j + 1]) i++;
    else j++;
  }
  return map;
}

// For each text, a list of booleans: true when that word is common to all texts. Every card is treated alike.
function labDiff(texts) {
  var w = texts.map(labWords), maps = [], i, k;
  for (i = 1; i < w.length; i++) maps.push(labLcs(w[0], w[i]));
  var common0 = w[0].map(function (_, x) { return maps.every(function (m) { return m[x] !== undefined; }); });
  var out = [common0];
  for (i = 1; i < w.length; i++) {
    var c = w[i].map(function () { return false; });
    for (k in maps[i - 1]) if (common0[k]) c[maps[i - 1][k]] = true;
    out.push(c);
  }
  return out;
}

// True when the shortest text has fewer than half the words of the longest.
function labMuchShorter(texts) {
  var n = texts.map(function (t) { return labWords(t).length; });
  return n.length > 1 && Math.min.apply(null, n) * 2 < Math.max.apply(null, n);
}

if (typeof module !== "undefined") module.exports = { labWords: labWords, labLcs: labLcs, labDiff: labDiff, labMuchShorter: labMuchShorter };

if (typeof document !== "undefined") (function () {
  "use strict";
  var screen = document.getElementById("screen");
  var countEl = document.getElementById("count");
  var barEl = document.getElementById("bar");
  var offlineEl = document.getElementById("offline");
  var lab = null, clips = [], answers = {}, idx = 0, slow = false, audio = null, retryTimer = null;
  var storeKey = "";
  var showDiff = true;
  try { showDiff = localStorage.getItem("atb-accuracy-lab:diff") !== "0"; } catch (e) { /* default on */ }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  // ---- storage: localStorage first, the server as a second copy ----
  function load() {
    try { return JSON.parse(localStorage.getItem(storeKey) || "{}").answers || {}; } catch (e) { return {}; }
  }
  function save() {
    try { localStorage.setItem(storeKey, JSON.stringify({ answers: answers })); } catch (e) { /* kept in memory only */ }
  }
  function unsent() {
    return Object.keys(answers).filter(function (id) { return !answers[id].sent; });
  }
  function showOffline() { offlineEl.hidden = unsent().length === 0; }

  function send(id) {
    var a = answers[id];
    if (!a) return Promise.resolve();
    var body = { clip: id, choice: a.choice, mistakes: !!a.mistakes, at: a.at };
    return fetch("answer", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(function (r) {
        if (!r.ok) throw new Error("rejected");
        if (answers[id] && answers[id].at === body.at) { answers[id].sent = true; save(); }
      })
      .catch(function () { /* retried below */ })
      .then(showOffline);
  }
  function flush() {
    unsent().forEach(send);
    showOffline();
  }

  function answered() { return clips.filter(function (c) { return answers[c.id]; }).length; }

  function choose(choice) {
    var c = clips[idx];
    if (!c) return;
    var box = document.getElementById("mistakes");
    answers[c.id] = { choice: choice, mistakes: !!(box && box.checked) && choice !== "none", at: Date.now(), sent: false };
    save();
    send(c.id);
    stopAudio();
    idx += 1;
    render();
  }

  function back() {
    if (idx === 0) return;
    stopAudio();
    idx -= 1;
    render();
  }

  // ---- audio ----
  function stopAudio() {
    if (audio) { audio.pause(); audio.removeAttribute("src"); audio.load(); audio = null; }
  }
  function fmt(s) {
    s = Math.max(0, Math.floor(s || 0));
    return Math.floor(s / 60) + ":" + ("0" + (s % 60)).slice(-2);
  }
  function togglePlay() {
    if (!audio) return;
    if (audio.ended) audio.currentTime = 0;
    if (audio.paused) { var p = audio.play(); if (p && p.catch) p.catch(function () {}); } else { audio.pause(); }
  }

  function playerBox(c) {
    var box = el("div", "player");
    var play = el("button", "play", "Play");
    play.type = "button";
    var track = el("div", "track");
    var fill = el("i");
    track.appendChild(fill);
    var time = el("span", "time", "0:00 / " + fmt(c.seconds));
    var rate = el("button", "btn", "0.75x");
    rate.type = "button";
    rate.setAttribute("aria-pressed", slow ? "true" : "false");
    box.appendChild(play); box.appendChild(track); box.appendChild(time); box.appendChild(rate);

    audio = new Audio();
    audio.preload = "auto";
    audio.src = c.audio;
    audio.playbackRate = slow ? 0.75 : 1;
    function sync() {
      var d = audio.duration && isFinite(audio.duration) ? audio.duration : c.seconds;
      fill.style.width = (100 * (audio.currentTime || 0) / d) + "%";
      time.textContent = fmt(audio.currentTime) + " / " + fmt(d);
      play.textContent = audio.ended ? "Replay" : (audio.paused ? "Play" : "Pause");
      play.classList.toggle("on", !audio.paused && !audio.ended);
    }
    ["timeupdate", "play", "pause", "ended", "loadedmetadata"].forEach(function (n) { audio.addEventListener(n, sync); });
    audio.addEventListener("error", function () { time.textContent = "audio not available"; });
    play.addEventListener("click", togglePlay);
    track.addEventListener("click", function (ev) {
      var r = track.getBoundingClientRect(), d = audio.duration && isFinite(audio.duration) ? audio.duration : c.seconds;
      audio.currentTime = d * Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width));
    });
    rate.addEventListener("click", function () {
      slow = !slow;
      audio.playbackRate = slow ? 0.75 : 1;
      rate.setAttribute("aria-pressed", slow ? "true" : "false");
    });
    return box;
  }

  // Fill a card with its words; words that are not common to all cards are wrapped in <mark class="diff">.
  function fillWords(box, text, common) {
    box.textContent = "";
    labWords(text).forEach(function (word, i) {
      if (i) box.appendChild(document.createTextNode(" "));
      if (showDiff && !common[i]) { box.appendChild(el("mark", "diff", word)); }
      else { box.appendChild(document.createTextNode(word)); }
    });
  }
  function toggleDiff() {
    showDiff = !showDiff;
    try { localStorage.setItem("atb-accuracy-lab:diff", showDiff ? "1" : "0"); } catch (e) { /* not kept */ }
    if (clips[idx]) render();
  }

  // ---- screens ----
  function renderBar() {
    barEl.textContent = "";
    clips.forEach(function (c, i) {
      var b = el("b", answers[c.id] ? "done" : "");
      if (i === idx) b.className = "now";
      barEl.appendChild(b);
    });
  }

  function render() {
    screen.textContent = "";
    renderBar();
    if (!clips.length) {
      countEl.textContent = "";
      screen.appendChild(el("p", "done-msg", "There are no clips in this test."));
      return;
    }
    if (idx >= clips.length) { renderDone(); return; }
    var c = clips[idx], prev = answers[c.id];
    countEl.textContent = "clip " + (idx + 1) + " of " + clips.length;

    screen.appendChild(el("div", "label", "listen"));
    screen.appendChild(playerBox(c));
    screen.appendChild(el("div", "question", "Which one matches what was said best?"));
    var texts = c.candidates.map(function (x) { return x.text; });
    var common = labDiff(texts);
    if (labMuchShorter(texts)) {
      screen.appendChild(el("div", "note", "One version is much shorter than the other. Listen for whether words are missing."));
    }
    var dt = el("button", "btn toggle", "Show differences (d)");
    dt.type = "button";
    dt.setAttribute("aria-pressed", showDiff ? "true" : "false");
    dt.addEventListener("click", toggleDiff);
    screen.appendChild(dt);

    c.candidates.forEach(function (cand, i) {
      var card = el("div", "card" + (prev && prev.choice === cand.key ? " chosen" : ""));
      var head = el("div", "head");
      head.appendChild(el("span", "n", String(i + 1)));
      var pick = el("button", "btn pick" + (prev && prev.choice === cand.key ? " chosen" : ""), "This one is best (" + (i + 1) + ")");
      pick.type = "button";
      pick.addEventListener("click", function () { choose(cand.key); });
      head.appendChild(pick);
      card.appendChild(head);
      var words = el("div", cand.text ? "words" : "words empty");
      if (cand.text) fillWords(words, cand.text, common[i]); else words.textContent = "(nothing was written)";
      words.setAttribute("dir", "auto");
      words.setAttribute("lang", "ur");
      card.appendChild(words);
      screen.appendChild(card);
    });

    var row = el("div", "row");
    [["same", "They are the same (s)"], ["none", "None of them is right (n)"]].forEach(function (p) {
      var b = el("button", "btn" + (prev && prev.choice === p[0] ? " chosen" : ""), p[1]);
      b.type = "button";
      b.addEventListener("click", function () { choose(p[0]); });
      row.appendChild(b);
    });
    screen.appendChild(row);

    var lab_ = el("label", "check");
    var box = el("input");
    box.type = "checkbox"; box.id = "mistakes"; box.checked = !!(prev && prev.mistakes);
    lab_.appendChild(box);
    lab_.appendChild(el("span", "", "The best one still has clear mistakes (m)"));
    screen.appendChild(lab_);

    var nav = el("div", "nav");
    var bk = el("button", "btn", "Back");
    bk.type = "button"; bk.disabled = idx === 0;
    bk.addEventListener("click", back);
    nav.appendChild(bk);
    nav.appendChild(el("span", "", "keys: 1 2 3 choose, s same, n none, m mistakes, d differences, space play, left arrow back"));
    screen.appendChild(nav);
  }

  function renderDone() {
    countEl.textContent = "finished";
    screen.appendChild(el("p", "done-msg", "Done. " + answered() + " of " + clips.length + " answered."));
    var unanswered = clips.findIndex(function (c) { return !answers[c.id]; });
    if (unanswered >= 0) {
      var go = el("button", "btn pick", "Go to the first unanswered clip");
      go.type = "button";
      go.addEventListener("click", function () { idx = unanswered; render(); });
      screen.appendChild(go);
      screen.appendChild(document.createTextNode(" "));
    }
    var bk = el("button", "btn", "Back");
    bk.type = "button";
    bk.addEventListener("click", back);
    screen.appendChild(bk);
    screen.appendChild(el("p", "note", "You can close this tab. Thank you."));
  }

  document.addEventListener("keydown", function (ev) {
    if (ev.ctrlKey || ev.metaKey || ev.altKey || !clips.length) return;
    var k = ev.key, c = clips[idx];
    if (k === " ") { ev.preventDefault(); togglePlay(); return; }
    if (k === "ArrowLeft") { ev.preventDefault(); back(); return; }
    if (!c) return;
    if (k >= "1" && k <= "3" && c.candidates[Number(k) - 1]) choose(c.candidates[Number(k) - 1].key);
    else if (k === "s" || k === "S") choose("same");
    else if (k === "n" || k === "N") choose("none");
    else if (k === "d" || k === "D") toggleDiff();
    else if (k === "m" || k === "M") { var b = document.getElementById("mistakes"); if (b) b.checked = !b.checked; }
  });

  document.addEventListener("keyup", function (ev) { if (ev.key === " ") ev.preventDefault(); });

  // ---- start ----
  function start(data, serverAnswers) {
    lab = data;
    clips = data.clips || [];
    storeKey = "atb-accuracy-lab:" + (data.id || "lab");
    answers = load();
    Object.keys(serverAnswers || {}).forEach(function (id) {  // a second browser, or cleared storage
      var s = serverAnswers[id];
      if (!answers[id] || (s.at > answers[id].at)) answers[id] = { choice: s.choice, mistakes: !!s.mistakes, at: s.at, sent: true };
    });
    save();
    idx = clips.findIndex(function (c) { return !answers[c.id]; });
    if (idx < 0) idx = clips.length;
    render();
    flush();
    retryTimer = setInterval(flush, 4000);
  }

  fetch("lab.json", { cache: "no-store" })
    .then(function (r) { if (!r.ok) throw new Error("no lab"); return r.json(); })
    .then(function (data) {
      return fetch("answers.json", { cache: "no-store" })
        .then(function (r) { return r.ok ? r.json() : {}; })
        .catch(function () { return {}; })
        .then(function (a) { start(data, a.answers || {}); });
    })
    .catch(function () {
      screen.appendChild(el("p", "done-msg", "The test could not be loaded. Reload this page."));
    });
})();
