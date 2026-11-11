"""The accuracy lab (listening test). Fake engine and invented audio only."""
import http.client
import json
import random
import shutil
import subprocess
import sys
import threading
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "accuracy_lab"))

import engines  # noqa: E402
import make_lab  # noqa: E402
import report  # noqa: E402
import run_lab  # noqa: E402

SR = 16000


def bursts(total=180.0, burst=6.0, pause=2.0, seed=0):
    """Noise bursts with digital silence between them, 16 kHz float32."""
    rng = np.random.default_rng(seed)
    out = np.zeros(int(total * SR), dtype=np.float32)
    t = 1.0
    while t + burst < total:
        out[int(t * SR): int((t + burst) * SR)] = rng.uniform(-0.3, 0.3, int(burst * SR))
        t += burst + pause
    return out


def write_source(path, samples):
    pcm = (samples * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path


def wav_seconds(path):
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("ATB_FAKE_SECONDS", "0")
    monkeypatch.setenv("ATB_FAKE_LOOP_AT", "2.0")  # first-pass decodes loop, repairs do not: methods differ
    return engines.get_engine("fake")


# ---- clip picking ----

BLOCKS = [(0.0, 120.0), (130.0, 300.0), (310.0, 400.0)]
SPANS = [(s, s + 8.0) for s in range(0, 400, 12)]


def pick(n=6, clip=25.0, seed=3, blocks=BLOCKS, spans=SPANS):
    return make_lab.pick_windows(spans, blocks, clip, n, random.Random(seed))


def test_windows_inside_blocks_and_apart():
    wins = pick()
    assert len(wins) == 6
    for a, b in wins:
        assert abs((b - a) - 25.0) < 1e-6
        assert any(lo <= a and b <= hi for lo, hi in BLOCKS), (a, b)
    for (_, e1), (s2, _) in zip(wins, wins[1:]):
        assert s2 >= e1


def test_windows_are_spread_and_deterministic():
    wins = pick()
    assert wins == pick()
    assert wins != pick(seed=4)
    # one window in each sixth of the usable speech: the first and last are far apart
    assert wins[-1][0] - wins[0][0] > 200


def test_windows_start_at_pause_when_close():
    spans = [(10.0, 20.0), (23.0, 60.0)]
    wins = make_lab.pick_windows(spans, [(10.0, 60.0)], 20.0, 1, random.Random(1))
    starts = {10.0, 23.0}
    # a start within 3 s of a speech start sits exactly on it
    assert wins[0][0] in starts or all(abs(wins[0][0] - s) > 3.0 for s in starts)


def test_fewer_clips_when_too_short():
    wins = make_lab.pick_windows([(0.0, 60.0)], [(0.0, 60.0)], 25.0, 5, random.Random(1))
    assert 1 <= len(wins) <= 2
    assert make_lab.pick_windows([(0.0, 10.0)], [(0.0, 10.0)], 25.0, 3, random.Random(1)) == []


# ---- candidates and cards ----

def test_fast_gives_three_normal_gives_two(fake):
    clip = bursts(25.0, burst=20.0)
    fast = make_lab.clip_candidates(clip, 2.0, fake, "ur")
    normal = make_lab.clip_candidates(clip, 1.0, fake, "ur")
    assert set(fast) == {"today", "new", "new_slow"}
    assert set(normal) == {"today", "new"}


def test_identical_texts_merge_and_key_maps_every_key():
    rng = random.Random(1)
    cards, key = make_lab.make_cards({"today": "same", "new": "same", "new_slow": "other"}, rng)
    assert len(cards) == 2
    assert sorted(sum(key.values(), [])) == ["new", "new_slow", "today"]
    assert {c["key"] for c in cards} == set(key)
    one, _ = make_lab.make_cards({"today": "x", "new": "x"}, rng)
    assert len(one) == 1


# ---- audio ----

def test_listening_audio_lengths(tmp_path):
    clip = bursts(25.0, burst=20.0)
    fast = make_lab.write_wav(tmp_path / "f.wav", make_lab.listening_audio(clip, 2.0))
    normal = make_lab.write_wav(tmp_path / "n.wav", make_lab.listening_audio(clip, 1.0))
    assert abs(fast - 50.0) < 1.0 and abs(wav_seconds(tmp_path / "f.wav") - fast) < 0.01
    assert abs(normal - 25.0) < 0.01
    with wave.open(str(tmp_path / "f.wav")) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)


def test_refuses_output_inside_repo(tmp_path):
    with pytest.raises(SystemExit):
        make_lab.check_out_dir(ROOT / "tools" / "private-audio")
    with pytest.raises(SystemExit):
        make_lab.check_out_dir(ROOT)
    assert make_lab.check_out_dir(ROOT / "tools" / "accuracy_lab" / "out" / "x").name == "x"
    assert make_lab.check_out_dir(tmp_path / "lab") == (tmp_path / "lab").resolve()


# ---- a whole lab ----

@pytest.fixture
def lab(tmp_path, fake):
    a = write_source(tmp_path / "a.wav", bursts(180, seed=1))
    b = write_source(tmp_path / "b.wav", bursts(180, seed=2))
    spec = {"clip_seconds": 25, "lessons": [
        {"audio": str(a), "speed": 2.0, "clips": 4, "group": "fast"},
        {"audio": str(b), "speed": 1.0, "clips": 3, "group": "normal"}]}
    out = tmp_path / "lab"
    make_lab.build_lab(spec, out, fake, seed=5)
    return out


def test_lab_files_are_blind(lab):
    raw = (lab / "lab.json").read_text(encoding="utf-8")
    for word in ("today", "new_slow", '"new"'):
        assert word not in raw
    data = json.loads(raw)
    assert len(data["clips"]) >= 5
    key = json.loads((lab / "key.json").read_text(encoding="utf-8"))["clips"]
    for clip in data["clips"]:
        assert set(clip) == {"id", "group", "audio", "seconds", "candidates"}
        assert all(set(c) == {"key", "text"} for c in clip["candidates"])
        assert (lab / clip["audio"]).exists()
        assert {c["key"] for c in clip["candidates"]} == set(key[clip["id"]]["keys"])
        assert len(clip["candidates"]) == (3 if clip["group"] == "fast" else 2) or len(clip["candidates"]) == 2
    for name in ("index.html", "lab.js", "lab.css"):
        assert (lab / name).exists()
    fast = [c for c in data["clips"] if c["group"] == "fast"]
    assert fast and all(abs(c["seconds"] - 50.0) < 1.5 for c in fast)


def test_existing_lab_is_not_overwritten(lab, fake):
    with pytest.raises(SystemExit):
        make_lab.build_lab({"clip_seconds": 25, "lessons": [{"audio": str(lab / "x.wav")}]}, lab, fake)


# ---- server ----

class Running:
    def __init__(self, folder):
        self.server = run_lab.make_server(folder, 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def request(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        h = {"Host": host or f"127.0.0.1:{self.port}", **(headers or {})}
        c.request(method, path, body=body, headers=h, )
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def served(lab):
    s = Running(lab)
    yield s
    s.close()


def post(s, body, **kw):
    return s.request("POST", "/answer", json.dumps(body).encode(), {"Content-Type": "application/json"}, **kw)


def test_server_serves_page_clip_with_range(served, lab):
    status, _, body = served.request("GET", "/")
    assert status == 200 and b"listening test" in body
    assert served.request("GET", "/lab.json")[0] == 200
    clip = json.loads((lab / "lab.json").read_text())["clips"][0]["audio"]
    status, headers, body = served.request("GET", "/" + clip, headers={"Range": "bytes=0-99"})
    assert status == 206 and len(body) == 100
    assert headers["Content-Range"].startswith("bytes 0-99/")
    status, _, body = served.request("GET", "/" + clip, headers={"Range": "bytes=-10"})
    assert status == 206 and len(body) == 10
    assert served.request("GET", "/" + clip, headers={"Range": "bytes=999999999-"})[0] == 416
    assert served.request("GET", "/" + clip)[0] == 200


def test_server_refuses_key_and_other_files(served, lab):
    for path in ("/key.json", "/KEY.json", "/%6bey.json", "/answers.jsonl", "/clips/../key.json",
                 "/clips/%2e%2e/key.json", "/../key.json", "/clips/"):
        assert served.request("GET", path)[0] in (403, 404), path
        assert b"today" not in served.request("GET", path)[2]


def test_server_checks_host_and_origin(served):
    assert served.request("GET", "/", headers={"Origin": "https://evil.example"})[0] == 403
    assert served.request("GET", "/", host="evil.example")[0] == 403
    assert served.request("GET", "/", headers={"Origin": f"http://127.0.0.1:{served.port}"})[0] == 200
    assert served.request("GET", "/", headers={"Origin": f"http://localhost:{served.port}"})[0] == 200
    assert post(served, {"clip": "clip-01", "choice": "same"}, host="evil.example")[0] == 403


def test_answers_stored_last_one_wins_and_survive_restart(served, lab):
    clip = json.loads((lab / "lab.json").read_text())["clips"][0]
    keys = [c["key"] for c in clip["candidates"]]
    assert post(served, {"clip": clip["id"], "choice": keys[0], "mistakes": False, "at": 1000})[0] == 200
    assert post(served, {"clip": clip["id"], "choice": keys[1], "mistakes": True, "at": 2000})[0] == 200
    assert post(served, {"clip": clip["id"], "choice": keys[1], "mistakes": True, "at": 2000})[0] == 200  # repeat
    assert post(served, {"clip": clip["id"], "choice": keys[0], "at": 1500})[0] == 200  # late old one loses
    got = json.loads((lab / "answers.json").read_text())["answers"]
    assert got[clip["id"]]["choice"] == keys[1] and got[clip["id"]]["mistakes"] is True
    assert len((lab / "answers.jsonl").read_text().splitlines()) == 4
    assert post(served, {"clip": "nope", "choice": "same"})[0] == 400
    assert post(served, {"clip": clip["id"], "choice": "ZZ"})[0] == 400
    assert served.request("POST", "/answer", b"{}", {"Content-Type": "text/plain"})[0] == 415
    assert json.loads(served.request("GET", "/answers.json")[2])["answers"][clip["id"]]["choice"] == keys[1]
    again = run_lab.Lab(lab)  # a restart keeps the answers
    assert again.answers[clip["id"]]["choice"] == keys[1]


def test_reserved_ports_refused(lab):
    for port in (8765, 8766):
        with pytest.raises(SystemExit):
            run_lab.make_server(lab, port)


# ---- report ----

def make_report_lab(tmp_path, fast_choices, normal_choices):
    key, answers = {}, {}
    n = 0
    for group, choices, names in (("fast", fast_choices, ["today", "new", "new_slow"]),
                                  ("normal", normal_choices, ["today", "new"])):
        for choice in choices:
            n += 1
            cid = f"clip-{n:02d}"
            key[cid] = {"keys": {chr(65 + i): [name] for i, name in enumerate(names)}, "group": group}
            if choice in names:
                answers[cid] = {"choice": chr(65 + names.index(choice)), "mistakes": choice == "new", "at": n}
            elif choice:
                answers[cid] = {"choice": choice, "mistakes": False, "at": n}
    (tmp_path / "key.json").write_text(json.dumps({"clips": key}))
    (tmp_path / "answers.json").write_text(json.dumps({"answers": answers}))
    return tmp_path


def test_report_counts_and_winner(tmp_path):
    lab = make_report_lab(tmp_path, ["new_slow"] * 4 + ["new", "today", "same", "none", None],
                          ["new"] * 5 + ["today", "same"])
    text = report.build_report(lab)
    assert "fast: 8 of 9 clips answered" in text
    assert "chosen: today 1, new 1, new_slow 4" in text
    assert "same: 1   none: 1   still has clear mistakes: 1" in text
    assert "For fast lessons, new_slow won" in text
    assert "For normal lessons, new won (new 5, today 1)" in text
    assert "overall: 15 of 16 clips answered" in text


def test_report_too_thin_and_close(tmp_path):
    thin = report.build_report(make_report_lab(tmp_path, ["new", "new", "same"], ["new"] * 6))
    assert "For fast lessons, only 3 answered: too thin to decide." in thin
    close = report.build_report(make_report_lab(tmp_path, ["new_slow", "new", "today", "new_slow"], ["new"] * 6))
    assert "no clear winner" in close


def test_report_without_answers(tmp_path):
    lab = make_report_lab(tmp_path, ["new"], ["new"])
    (lab / "answers.json").unlink()
    assert "too thin" in report.build_report(lab)


# ---- page logic (Node only) ----

@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_word_diff_logic():
    script = """
const d = require(process.argv[1]);
const out = {
  two: d.labDiff(["a b c d", "a x c d e"]),
  three: d.labDiff(["a b c d", "a x c d", "a b c"]),
  same: d.labDiff(["a b", "a b"]),
  short: [d.labMuchShorter(["a b c d e f", "a b"]), d.labMuchShorter(["a b c d", "a b c"])],
};
console.log(JSON.stringify(out));
"""
    r = subprocess.run(["node", "-e", script, str(ROOT / "tools" / "accuracy_lab" / "lab.js")],
                       capture_output=True, text=True, check=True)
    got = json.loads(r.stdout)
    assert got["two"] == [[True, False, True, True], [True, False, True, True, False]]
    assert got["three"] == [[True, False, True, False], [True, False, True, False], [True, False, True]]
    assert got["same"] == [[True, True], [True, True]]
    assert got["short"] == [True, False]


# ---- scripts end to end ----

def test_scripts_run_with_fake_engine(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ATB_FAKE_SECONDS", "0")
    monkeypatch.setenv("ATB_FAKE_LOOP_AT", "2.0")
    a = write_source(tmp_path / "a.wav", bursts(120, seed=1))
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"clip_seconds": 20, "lessons": [{"audio": str(a), "speed": 1.5, "clips": 3}]}))
    out = tmp_path / "lab"
    assert make_lab.main(["--spec", str(spec), "--out", str(out), "--engine", "fake", "--seed", "2"]) == 0
    s = Running(out)
    try:
        assert s.request("GET", "/")[0] == 200
        assert s.request("GET", "/key.json")[0] == 404
    finally:
        s.close()
    assert report.main(["--lab", str(out)]) == 0
    assert "Decisions" in capsys.readouterr().out
