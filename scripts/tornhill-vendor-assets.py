#!/usr/bin/env python3
"""
tornhill-vendor-assets — download the pinned viewer JS libraries into ./vendor.

tornhill-to-html can embed these libraries (``--assets inline``) or reference them
locally (``--assets local``) so the rendered HTML makes ZERO third-party network
requests. This is the one explicit network step; it is kept out of the renderer so
generation itself stays offline. The downloaded bytes are verified against the
same Subresource-Integrity (SRI) hashes the CDN mode pins, so a tampered or
swapped CDN payload fails loudly instead of being vendored silently.

Usage:
    python tornhill-vendor-assets.py [--vendor-dir vendor]

Stdlib only. Run once; the resulting ./vendor dir is git-ignored.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import sys
import urllib.request
from pathlib import Path

# Single source of truth for pinned versions + their SRI hashes. tornhill-to-html
# imports this so CDN-mode integrity attributes and vendored bytes can never drift.
ASSETS = {
    "mermaid.min.js": {
        "url": "https://cdn.jsdelivr.net/npm/mermaid@10.9.3/dist/mermaid.min.js",
        "sri": "sha384-R63zfMfSwJF4xCR11wXii+QUsbiBIdiDzDbtxia72oGWfkT7WHJfmD/I/eeHPJyT",
    },
    "svg-pan-zoom.min.js": {
        "url": "https://cdn.jsdelivr.net/npm/svg-pan-zoom@3.6.1/dist/svg-pan-zoom.min.js",
        "sri": "sha384-yc/c2Lk1s2V2ir1rxvjo8YyVD9PlOlYTqpNr3Wm1WIuAA30GlDYNx6U5104OiavY",
    },
}


def sri_of(data: bytes) -> str:
    return "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode("ascii")


def fetch(name: str, spec: dict, vendor_dir: Path) -> None:
    print(f"fetching {name} <- {spec['url']}")
    with urllib.request.urlopen(spec["url"], timeout=60) as resp:  # noqa: S310 (pinned https)
        data = resp.read()
    got = sri_of(data)
    if got != spec["sri"]:
        raise SystemExit(
            f"SRI MISMATCH for {name}\n  expected {spec['sri']}\n  got      {got}\n"
            "Refusing to vendor unverified bytes."
        )
    out = vendor_dir / name
    out.write_bytes(data)
    print(f"  ok  {len(data):,} bytes  {got}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Vendor pinned viewer JS for offline tornhill HTML.")
    ap.add_argument("--vendor-dir", default="vendor", help="output dir (default: ./vendor)")
    args = ap.parse_args()

    vendor_dir = Path(args.vendor_dir)
    vendor_dir.mkdir(parents=True, exist_ok=True)
    for name, spec in ASSETS.items():
        fetch(name, spec, vendor_dir)
    print(f"\ndone -> {vendor_dir.resolve()}  (use: tornhill-to-html.py --assets inline|local)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
