#!/usr/bin/env python3
"""
Download SMART and CORPUS reference data from Network Rail Open Data.

SMART — maps TD berths to physical locations (mileages, timing points).
CORPUS — location reference data (stations, TIPLOCs, STANOX codes).

Usage:
    python scripts/download_reference_data.py

Requires NROD_USERNAME and NROD_PASSWORD in .env (or environment).
"""

import gzip
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import base64

# Load .env manually (no extra dependency)
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

NROD_USERNAME = os.environ.get("NROD_USERNAME", "")
NROD_PASSWORD = os.environ.get("NROD_PASSWORD", "")

if not NROD_USERNAME or not NROD_PASSWORD or NROD_USERNAME == "your-username":
    print("ERROR: Set NROD_USERNAME and NROD_PASSWORD in .env first.")
    print("       Register at https://publicdatafeeds.networkrail.co.uk")
    sys.exit(1)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# NROD static data endpoints
FEEDS = {
    "smart": {
        "url": "https://publicdatafeeds.networkrail.co.uk/ntrod/SupportingFileAuthenticate?type=SMART",
        "output": "smart.json",
        "description": "SMART berth-to-location mapping",
    },
    "corpus": {
        "url": "https://publicdatafeeds.networkrail.co.uk/ntrod/SupportingFileAuthenticate?type=CORPUS",
        "output": "corpus.json",
        "description": "CORPUS location reference",
    },
}


def download_feed(name: str, feed: dict) -> Path:
    """Download a single NROD reference feed."""
    output_path = DATA_DIR / feed["output"]
    print(f"Downloading {feed['description']}...")

    credentials = base64.b64encode(f"{NROD_USERNAME}:{NROD_PASSWORD}".encode()).decode()
    req = Request(feed["url"])
    req.add_header("Authorization", f"Basic {credentials}")

    try:
        with urlopen(req) as resp:
            raw = resp.read()
            # NROD returns gzip-compressed JSON
            try:
                data = gzip.decompress(raw)
            except gzip.BadGzipFile:
                data = raw  # already decompressed

            # Validate it's JSON
            parsed = json.loads(data)
            # Pretty-print for readability
            output_path.write_text(json.dumps(parsed, indent=2))
            size_mb = output_path.stat().st_size / (1024 * 1024)
            print(f"  ✓ Saved {output_path.name} ({size_mb:.1f} MB)")
            return output_path

    except HTTPError as e:
        if e.code == 401:
            print(f"  ✗ Authentication failed (401) — check NROD credentials in .env")
        elif e.code == 403:
            print(f"  ✗ Access denied (403) — your NROD account may not be activated yet")
        else:
            print(f"  ✗ HTTP error {e.code}: {e.reason}")
        sys.exit(1)


def main():
    print(f"NROD user: {NROD_USERNAME[:3]}***")
    print(f"Output dir: {DATA_DIR}\n")

    for name, feed in FEEDS.items():
        download_feed(name, feed)

    print("\n✓ All reference data downloaded.")
    print("Next: run 'python scripts/find_berths.py' to identify berths near the crossing.")


if __name__ == "__main__":
    main()
