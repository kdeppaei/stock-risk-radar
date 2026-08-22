from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from openkiri_integrity import looks_like_32_hex, md5_hex, sha256_hex


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect a digest-shaped string and run a safe local hash demo.",
    )
    parser.add_argument("candidate", help="Observed string to inspect")
    parser.add_argument(
        "--demo",
        default="OpenKiri-Demo",
        help="Local text used to demonstrate deterministic MD5 and SHA-256 outputs",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    candidate = args.candidate.strip()
    shaped = looks_like_32_hex(candidate)

    print("OpenKiri Hash Inspector")
    print("-----------------------")
    print(f"Observed value: {candidate}")
    print(f"32-character hexadecimal shape: {'yes' if shaped else 'no'}")
    if shaped:
        print("Interpretation: possible MD5-shaped digest, but shape alone does not prove MD5.")
    else:
        print("Interpretation: not a 32-character hexadecimal digest shape.")

    print()
    print("Local verification demo (not authentication or encryption)")
    print(f"Input text: {args.demo}")
    print(f"MD5:      {md5_hex(args.demo)}")
    print(f"SHA-256:  {sha256_hex(args.demo)}")
    print("Note: MD5 is a hash function. OpenKiri uses SHA-256 for snapshot integrity metadata.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

