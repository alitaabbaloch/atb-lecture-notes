"""Usage: python addons/notes/make_roman.py <transcript.json> [more files...]
Rewrites an Urdu-script transcript in Roman Urdu with the notes AI. The old file is kept as <name>.urdu.md.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent.parent / "server"), str(HERE)]

import config  # noqa: E402
import llm  # noqa: E402
import romanize  # noqa: E402


def main(args: list[str]) -> int:
    if not args:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    cfg = config.load()
    llm.set_provider(cfg.notes_provider)
    try:
        for arg in args:
            p = Path(arg)
            out, bad = romanize.convert_file(p)
            print(f"{p.name} -> {out.name} ({bad} lines left in Urdu)")
    except llm.NoProviderError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
