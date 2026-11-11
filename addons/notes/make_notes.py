"""Usage: python addons/notes/make_notes.py <transcript.md> [more files...]
Writes <NN>-<lesson>-notes.md and .json next to each transcript, with the notes AI named in secrets.env.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent.parent / "server"), str(HERE)]

import config  # noqa: E402
import llm  # noqa: E402
import summarize  # noqa: E402


def main(args: list[str]) -> int:
    if not args:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    cfg = config.load()
    llm.set_provider(cfg.notes_provider)
    try:
        for arg in args:
            p = Path(arg)
            print(f"{p.name} -> {summarize.summarize(p).name}")
    except llm.NoProviderError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
