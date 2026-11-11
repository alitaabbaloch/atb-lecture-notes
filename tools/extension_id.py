#!/usr/bin/env python3
"""Print the Chrome extension ID for the key in extension/manifest.json.

Usage: extension_id.py [--key BASE64_PUBLIC_KEY] [--manifest PATH]
"""
import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path


def extension_id(key_b64: str) -> str:
    der = base64.b64decode(key_b64.strip())
    digest = hashlib.sha256(der).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def main() -> int:
    default_manifest = Path(__file__).resolve().parent.parent / "extension" / "manifest.json"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", help="base64 public key (default: the key in the manifest)")
    parser.add_argument("--manifest", default=str(default_manifest))
    args = parser.parse_args()
    key = args.key
    if not key:
        try:
            key = json.loads(Path(args.manifest).read_text(encoding="utf-8")).get("key")
        except (OSError, ValueError) as exc:
            print(f"cannot read manifest: {exc}", file=sys.stderr)
            return 1
        if not key:
            print("manifest has no key", file=sys.stderr)
            return 1
    try:
        print(extension_id(key))
    except ValueError:
        print("the key is not valid base64", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
