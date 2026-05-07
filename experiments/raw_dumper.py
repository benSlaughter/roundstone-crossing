"""
Raw NROD message dumper — captures complete unfiltered messages from
all available subscription topics for inspection.

Saves to experiments/raw_dumps/ with one file per topic.
Runs for a configurable duration then exits.

Usage:
    python experiments/raw_dumper.py [duration_seconds]
    python experiments/raw_dumper.py 120   # 2 minutes
"""

import gzip
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import stomp

PROJECT_ROOT = Path(__file__).parent.parent

# All known NROD STOMP topics
TOPICS = {
    "TD_ALL_SIG_AREA": "/topic/TD_ALL_SIG_AREA",       # Train Describer — all areas
    "TRAIN_MVT_ALL_TOC": "/topic/TRAIN_MVT_ALL_TOC",   # TRUST — all TOCs
    "VSTP_ALL": "/topic/VSTP_ALL",                      # Very Short Term Plan
    "TSR_ALL_ROUTE": "/topic/TSR_ALL_ROUTE",            # Temporary Speed Restrictions
    "RTPPM_ALL": "/topic/RTPPM_ALL",                    # Real Time PPM
}

DUMP_DIR = Path(__file__).parent / "raw_dumps"
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 60


class DumpListener(stomp.ConnectionListener):
    def __init__(self):
        self.files = {}
        self.counts = {}
        DUMP_DIR.mkdir(exist_ok=True)

        for name in TOPICS:
            path = DUMP_DIR / f"{name}.jsonl"
            self.files[name] = open(path, "w")
            self.counts[name] = 0

    def on_message(self, frame):
        try:
            dest = frame.headers.get("destination", "")
            topic_name = None
            for name, path in TOPICS.items():
                if dest == path:
                    topic_name = name
                    break
            if not topic_name:
                topic_name = "UNKNOWN"
                if topic_name not in self.files:
                    self.files[topic_name] = open(DUMP_DIR / "UNKNOWN.jsonl", "w")
                    self.counts[topic_name] = 0

            body = frame.body
            if isinstance(body, bytes):
                try:
                    body = gzip.decompress(body).decode("utf-8")
                except (gzip.BadGzipFile, OSError):
                    body = body.decode("utf-8", errors="replace")

            messages = json.loads(body)
            if not isinstance(messages, list):
                messages = [messages]

            for msg in messages:
                record = {
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "topic": topic_name,
                    "msg": msg,
                }
                self.files[topic_name].write(json.dumps(record) + "\n")
                self.counts[topic_name] += 1

        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)

    def on_error(self, frame):
        print(f"STOMP error: {frame.body}", file=sys.stderr)

    def close(self):
        for f in self.files.values():
            f.close()


def main():
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

    username = os.environ.get("NROD_USERNAME", "")
    password = os.environ.get("NROD_PASSWORD", "")
    if not username or not password:
        print("NROD_USERNAME and NROD_PASSWORD must be set")
        sys.exit(1)

    listener = DumpListener()
    conn = stomp.Connection(
        host_and_ports=[("publicdatafeeds.networkrail.co.uk", 61618)],
        heartbeats=(15000, 15000),
    )
    conn.set_listener("dumper", listener)

    print(f"Connecting to NROD...")
    conn.connect(username=username, passcode=password, wait=True)

    for name, dest in TOPICS.items():
        try:
            conn.subscribe(destination=dest, id=f"dump-{name}", ack="auto")
            print(f"  Subscribed: {name}")
        except Exception as e:
            print(f"  Failed to subscribe {name}: {e}")

    print(f"\nDumping for {DURATION}s to {DUMP_DIR}/")
    print("Press Ctrl+C to stop early.\n")

    try:
        start = time.time()
        while time.time() - start < DURATION:
            time.sleep(10)
            total = sum(listener.counts.values())
            breakdown = ", ".join(f"{k}:{v}" for k, v in sorted(listener.counts.items()) if v > 0)
            print(f"  [{int(time.time()-start)}s] {total} msgs — {breakdown}")
    except KeyboardInterrupt:
        print("\nStopping early...")

    conn.disconnect()
    listener.close()

    print(f"\nDone. Files in {DUMP_DIR}/:")
    for name in TOPICS:
        path = DUMP_DIR / f"{name}.jsonl"
        if path.exists():
            lines = sum(1 for _ in open(path))
            size = path.stat().st_size
            print(f"  {name}.jsonl: {lines} messages ({size/1024:.1f} KB)")


if __name__ == "__main__":
    main()
