"""
Signal data logger — captures TD messages (CA/CB/CC/SF/SG) for specific
berths/addresses to investigate additional signalling data.

Usage:
    cd ~/projects/roundstone-crossing
    source .venv/bin/activate
    python experiments/signal_logger.py

Logs to experiments/signal_data.db (SQLite) and experiments/signal_log.jsonl.
Press Ctrl+C to stop.
"""

import gzip
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import stomp

# Add project root to path for .env loading
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("signal_logger")

# ── Configuration ────────────────────────────────────────────────────

# Berths/signals to watch — add more as needed
WATCH_BERTHS = {"BH74", "BH75", "AR07", "AR05", "AR03", "H987", "H989", "ARAP"}

# Also log ALL SF/SG data for areas containing these berths
# LA = our area (Angmering/Littlehampton)
# BM = Barnham area (BH74/BH75 etc)
# ZH = overlapping area with H987/H989 → AR berths
WATCH_AREAS = {"LA", "BM", "ZH"}

# Log CA/CB/CC for any berth in these areas (captures movements near targets)
LOG_ALL_MOVEMENTS_IN_AREAS = True

DB_PATH = Path(__file__).parent / "signal_data.db"
JSONL_PATH = Path(__file__).parent / "signal_log.jsonl"

# ── Database ─────────────────────────────────────────────────────────

def init_db(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(path), check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS td_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            msg_type TEXT NOT NULL,
            area_id TEXT NOT NULL,
            from_berth TEXT,
            to_berth TEXT,
            headcode TEXT,
            td_time TEXT,
            raw_json TEXT
        );
        CREATE TABLE IF NOT EXISTS sf_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            msg_type TEXT NOT NULL,
            area_id TEXT NOT NULL,
            address TEXT NOT NULL,
            data_hex TEXT NOT NULL,
            data_bin TEXT NOT NULL,
            raw_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_td_ts ON td_events(timestamp);
        CREATE INDEX IF NOT EXISTS idx_td_berth ON td_events(to_berth);
        CREATE INDEX IF NOT EXISTS idx_sf_ts ON sf_events(timestamp);
        CREATE INDEX IF NOT EXISTS idx_sf_addr ON sf_events(address);
    """)
    db.commit()
    return db


# ── STOMP Listener ───────────────────────────────────────────────────

class SignalListener(stomp.ConnectionListener):
    def __init__(self, db: sqlite3.Connection, jsonl_file):
        self.db = db
        self.jsonl = jsonl_file
        self.msg_count = 0
        self.logged_count = 0

    def on_message(self, frame):
        try:
            body = frame.body
            if isinstance(body, bytes):
                try:
                    body = gzip.decompress(body).decode("utf-8")
                except (gzip.BadGzipFile, OSError):
                    body = body.decode("utf-8", errors="replace")

            messages = json.loads(body)
            if not isinstance(messages, list):
                return

            for msg in messages:
                self.msg_count += 1
                self._process_msg(msg)

        except Exception as e:
            logger.error(f"Message processing error: {e}")

    def _process_msg(self, msg: dict):
        now = datetime.now(timezone.utc).isoformat()

        # CA — berth step
        if "CA_MSG" in msg:
            self._handle_td(now, "CA", msg["CA_MSG"])
        if "CB_MSG" in msg:
            self._handle_td(now, "CB", msg["CB_MSG"])
        if "CC_MSG" in msg:
            self._handle_td(now, "CC", msg["CC_MSG"])

        # SF/SG — signalling data
        if "SF_MSG" in msg:
            self._handle_sf(now, "SF", msg["SF_MSG"])
        if "SG_MSG" in msg:
            self._handle_sf(now, "SG", msg["SG_MSG"])

    def _handle_td(self, now: str, msg_type: str, data: dict):
        area = data.get("area_id", "")
        from_berth = data.get("from", "")
        to_berth = data.get("to", "")
        headcode = data.get("descr", "")
        td_time = data.get("time", "")

        # Filter: only log if berth is in our watch list or area matches
        berths_involved = {from_berth, to_berth} - {""}
        if not (berths_involved & WATCH_BERTHS or
                (LOG_ALL_MOVEMENTS_IN_AREAS and area in WATCH_AREAS)):
            return

        self.db.execute(
            "INSERT INTO td_events (timestamp, msg_type, area_id, from_berth, to_berth, headcode, td_time, raw_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now, msg_type, area, from_berth, to_berth, headcode, td_time, json.dumps(data)),
        )
        self.db.commit()
        self.logged_count += 1

        # Also write to JSONL
        record = {"ts": now, "type": msg_type, "area": area, "from": from_berth,
                   "to": to_berth, "hc": headcode, "td_time": td_time}
        self.jsonl.write(json.dumps(record) + "\n")
        self.jsonl.flush()

        marker = "*" if berths_involved & WATCH_BERTHS else ""
        logger.info(f"{msg_type} {area} {headcode:>4s} {from_berth:>4s} → {to_berth:<4s} {marker}")

    def _handle_sf(self, now: str, msg_type: str, data: dict):
        area = data.get("area_id", "")
        if area not in WATCH_AREAS:
            return

        address = data.get("address", "")
        sf_data = data.get("data", "")
        if not address or not sf_data:
            return

        data_bin = bin(int(sf_data, 16))[2:].zfill(8)

        self.db.execute(
            "INSERT INTO sf_events (timestamp, msg_type, area_id, address, data_hex, data_bin, raw_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now, msg_type, area, address, sf_data, data_bin, json.dumps(data)),
        )
        self.db.commit()
        self.logged_count += 1

        record = {"ts": now, "type": msg_type, "area": area, "addr": address,
                   "hex": sf_data, "bin": data_bin}
        self.jsonl.write(json.dumps(record) + "\n")
        self.jsonl.flush()

        logger.debug(f"{msg_type} {area} addr={address} data=0x{sf_data} ({data_bin})")

    def on_error(self, frame):
        logger.error(f"STOMP error: {frame.body}")

    def on_disconnected(self):
        logger.warning("STOMP disconnected")
        self.connected = False

    def on_connected(self, frame):
        logger.info("STOMP connected")
        self.connected = True


# ── Main ─────────────────────────────────────────────────────────────

RECONNECT_DELAYS = [5, 10, 30, 60, 120, 300]  # escalating backoff (seconds)


def main():
    # Load .env
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
        logger.error("NROD_USERNAME and NROD_PASSWORD must be set in .env")
        sys.exit(1)

    logger.info(f"Watching berths: {', '.join(sorted(WATCH_BERTHS))}")
    logger.info(f"Watching areas: {', '.join(sorted(WATCH_AREAS))}")
    logger.info(f"Logging all area movements: {LOG_ALL_MOVEMENTS_IN_AREAS}")
    logger.info(f"DB: {DB_PATH}")
    logger.info(f"JSONL: {JSONL_PATH}")

    db = init_db(DB_PATH)
    jsonl = open(JSONL_PATH, "a")

    listener = SignalListener(db, jsonl)
    listener.connected = False

    def connect(conn):
        conn.connect(username=username, passcode=password, wait=True)
        conn.subscribe(destination="/topic/TD_ALL_SIG_AREA", id="sig-log-sub", ack="auto")

    conn = stomp.Connection(
        host_and_ports=[("publicdatafeeds.networkrail.co.uk", 61618)],
        heartbeats=(15000, 15000),
        reconnect_attempts_max=-1,
    )
    conn.set_listener("signal_logger", listener)

    reconnect_attempt = 0

    try:
        connect(conn)
        reconnect_attempt = 0
        logger.info("Connected and subscribed — logging signals. Ctrl+C to stop.")

        while True:
            time.sleep(60)
            logger.info(f"Stats: {listener.msg_count} total msgs, {listener.logged_count} logged")

            if not listener.connected:
                delay = RECONNECT_DELAYS[min(reconnect_attempt, len(RECONNECT_DELAYS) - 1)]
                logger.warning(f"Connection lost — reconnecting in {delay}s (attempt {reconnect_attempt + 1})")
                time.sleep(delay)
                try:
                    connect(conn)
                    reconnect_attempt = 0
                    logger.info("Reconnected successfully")
                except Exception as e:
                    reconnect_attempt += 1
                    logger.error(f"Reconnect failed: {e}")

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass
        jsonl.close()
        db.close()
        logger.info("Done.")


if __name__ == "__main__":
    main()
