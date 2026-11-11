"""Fill what the first transcript is missing from the second hearing. Pure: no audio, no model, no files."""
import copy

import guard


def _merge(holes: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for start, end in sorted(holes):
        if out and start < out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def holes(primary: dict) -> list[tuple[float, float]]:
    """Unclear stretches and no-text gaps of the first transcript, in recording seconds, merged, in time order."""
    found = [(i["start"], i["end"]) for i in primary.get("issues", []) if i.get("kind") == "unclear"]
    blocks = [(b["start"], b["end"]) for b in primary.get("blocks") or []]
    if blocks:
        tempo = primary.get("tempo") or 1.0
        found += guard.find_gaps(primary.get("segments", []), blocks, primary.get("issues", []),
                                 min_seconds=guard.GAP_SECONDS * tempo)
    return _merge(found)


def _lines_in(second: dict, start: float, end: float) -> list[dict]:
    out = []
    for seg in second.get("segments", []):
        text = str(seg.get("text") or "").strip()
        if text and start <= (seg["start"] + seg["end"]) / 2 < end:
            out.append({"start": max(seg["start"], start), "end": min(seg["end"], end), "text": seg["text"],
                        "block": seg.get("block", 0), "repaired": True, "source": "second"})
    return out


def _span(issues: list[dict], kind: str) -> float:
    return round(sum(i["end"] - i["start"] for i in issues if i.get("kind") == kind), 2)


def fill(primary: dict, second: dict) -> tuple[dict, int]:
    """Fill what the first transcript is missing from the second one. Returns (new primary, stretches filled)."""
    new = copy.deepcopy(primary)
    segments, issues, filled = new.get("segments", []), new.get("issues", []), 0
    for start, end in holes(primary):
        lines = _lines_in(second, start, end)
        if not lines:
            continue
        segments.extend(copy.deepcopy(lines))
        issues[:] = [i for i in issues if not (i.get("kind") == "unclear" and i["start"] >= start - 1e-9
                                               and i["end"] <= end + 1e-9)]
        issues.append({"kind": "filled", "start": start, "end": end, "detail": "second_hearing"})
        filled += 1
    segments.sort(key=lambda s: (s["start"], s["end"]))
    for n, seg in enumerate(segments):
        seg["id"] = n
    issues.sort(key=lambda i: (i["start"], i["end"]))
    new["segments"], new["issues"] = segments, issues
    new["text"] = " ".join(s["text"].strip() for s in segments if s["text"].strip())
    quality = new.setdefault("quality", {})
    quality["unclear_seconds"] = _span(issues, "unclear")
    quality["words"] = sum(len(s["text"].split()) for s in segments)
    quality["second_filled_seconds"] = _span(issues, "filled")
    return new, filled
