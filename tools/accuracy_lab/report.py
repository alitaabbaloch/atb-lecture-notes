#!/usr/bin/env python3
"""Turn the answers into counts and one plain sentence per decision.

Usage: uv run python tools/accuracy_lab/report.py --lab <folder>
"""
import argparse
import json
import sys
from pathlib import Path

MIN_ANSWERS = 4
NAMES = ("today", "new", "new_slow")
DECISIONS = {"fast": ("new_slow", "new", "today"), "normal": ("new", "today")}


def load(lab: Path):
    lab = Path(lab)
    key = json.loads((lab / "key.json").read_text(encoding="utf-8"))["clips"]
    try:
        answers = json.loads((lab / "answers.json").read_text(encoding="utf-8"))["answers"]
    except FileNotFoundError:
        answers = {}
    return key, answers


def tally(key: dict, answers: dict, group: str | None) -> dict:
    t = {"clips": 0, "answers": 0, "same": 0, "none": 0, "mistakes": 0, "chosen": dict.fromkeys(NAMES, 0)}
    for cid, info in key.items():
        if group and info["group"] != group:
            continue
        t["clips"] += 1
        a = answers.get(cid)
        if not a:
            continue
        t["answers"] += 1
        t["mistakes"] += 1 if a.get("mistakes") else 0
        if a["choice"] in ("same", "none"):
            t[a["choice"]] += 1
        else:
            for name in info["keys"].get(a["choice"], []):  # a merged card counts for each method on it
                t["chosen"][name] += 1
    return t


def sentence(group: str, t: dict) -> str:
    order = DECISIONS[group]
    label = f"{group} lessons"
    if t["answers"] < MIN_ANSWERS:
        return f"For {label}, only {t['answers']} answered: too thin to decide."
    counts = sorted(((t["chosen"][n], n) for n in order), reverse=True)
    (top, best), (second, _) = counts[0], counts[1]
    detail = ", ".join(f"{n} {t['chosen'][n]}" for n in order)
    if top - second >= 2:
        return f"For {label}, {best} won ({detail})."
    return f"For {label}, no clear winner ({detail}); too close to call."


def build_report(lab: Path) -> str:
    key, answers = load(lab)
    lines = []
    groups = [g for g in DECISIONS if any(i["group"] == g for i in key.values())]
    for title, group in [(g, g) for g in groups] + [("overall", None)]:
        t = tally(key, answers, group)
        chosen = ", ".join(f"{n} {t['chosen'][n]}" for n in NAMES if group != "normal" or n != "new_slow")
        lines += [f"{title}: {t['answers']} of {t['clips']} clips answered",
                  f"  chosen: {chosen}",
                  f"  same: {t['same']}   none: {t['none']}   still has clear mistakes: {t['mistakes']}", ""]
    lines.append("Decisions")
    for g in groups:
        lines.append("  " + sentence(g, tally(key, answers, g)))
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--lab", required=True)
    a = p.parse_args(argv)
    if not (Path(a.lab) / "key.json").exists():
        raise SystemExit(f"No key.json in {a.lab}.")
    print(build_report(Path(a.lab)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
