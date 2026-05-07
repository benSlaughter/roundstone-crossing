"""
Analyse captured signal data — compare BH74/BH75/AR07/AR05 with our
existing crossing berths to find patterns in the SF bit data.

Usage:
    python experiments/analyse_signals.py
"""

import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

DB_PATH = Path(__file__).parent / "signal_data.db"


def main():
    if not DB_PATH.exists():
        print(f"No data yet — run signal_logger.py first.\nExpected: {DB_PATH}")
        sys.exit(1)

    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    print("=" * 70)
    print("SIGNAL DATA ANALYSIS")
    print("=" * 70)

    # ── TD event summary ──
    rows = db.execute("SELECT COUNT(*) as n, msg_type FROM td_events GROUP BY msg_type").fetchall()
    print(f"\n📡 TD Events: {sum(r['n'] for r in rows)} total")
    for r in rows:
        print(f"   {r['msg_type']}: {r['n']}")

    # Events per berth (top 20)
    print("\n📍 Top berths (by event count):")
    rows = db.execute("""
        SELECT to_berth as berth, COUNT(*) as n, GROUP_CONCAT(DISTINCT headcode) as headcodes
        FROM td_events WHERE to_berth != '' GROUP BY to_berth ORDER BY n DESC LIMIT 20
    """).fetchall()
    for r in rows:
        hcs = r['headcodes'][:40] if r['headcodes'] else ""
        marker = " ⭐" if r['berth'] in ("BH74", "BH75", "AR07", "AR05") else ""
        print(f"   {r['berth']:>6s}: {r['n']:>4d} events  [{hcs}]{marker}")

    # ── SF event summary ──
    rows = db.execute("SELECT COUNT(*) as n FROM sf_events").fetchone()
    print(f"\n🔴 SF Events: {rows['n']} total")

    # Unique addresses
    rows = db.execute("""
        SELECT address, COUNT(*) as n,
               COUNT(DISTINCT data_hex) as unique_values,
               GROUP_CONCAT(DISTINCT data_hex) as values
        FROM sf_events GROUP BY address ORDER BY n DESC
    """).fetchall()
    print(f"\n📊 SF Addresses ({len(rows)} unique):")
    for r in rows:
        vals = r['values'][:60] if r['values'] else ""
        print(f"   addr={r['address']:>4s}: {r['n']:>4d} events, {r['unique_values']:>2d} unique values  [{vals}]")

    # ── Bit-level analysis per address ──
    print("\n🔬 Bit-level analysis per SF address:")
    for addr_row in rows:
        addr = addr_row['address']
        events = db.execute(
            "SELECT data_bin, timestamp FROM sf_events WHERE address = ? ORDER BY timestamp",
            (addr,)
        ).fetchall()
        if len(events) < 2:
            continue

        print(f"\n   Address {addr} ({len(events)} events):")

        # Find which bits change
        bit_changes = [0] * 8
        prev_bin = events[0]['data_bin']
        for e in events[1:]:
            curr_bin = e['data_bin']
            for i in range(8):
                if i < len(prev_bin) and i < len(curr_bin) and prev_bin[i] != curr_bin[i]:
                    bit_changes[i] += 1
            prev_bin = curr_bin

        print(f"   Bit changes: [{' '.join(f'b{i}:{bit_changes[i]}' for i in range(8))}]")

        # Show first 10 transitions
        prev = events[0]
        transitions = 0
        for e in events[1:]:
            if e['data_bin'] != prev['data_bin']:
                transitions += 1
                if transitions <= 10:
                    t = e['timestamp'][11:19]
                    print(f"     {t}  {prev['data_bin']} → {e['data_bin']}")
            prev = e
        if transitions > 10:
            print(f"     ... and {transitions - 10} more transitions")

    # ── Correlation: TD events near SF changes ──
    print("\n🔗 Correlation: TD events within 30s of SF changes:")
    sf_changes = db.execute("""
        SELECT s1.timestamp, s1.address, s1.data_bin as new_val
        FROM sf_events s1
        WHERE EXISTS (
            SELECT 1 FROM sf_events s2
            WHERE s2.address = s1.address
            AND s2.timestamp < s1.timestamp
            AND s2.data_bin != s1.data_bin
            AND s2.id = s1.id - 1
        )
        ORDER BY s1.timestamp
        LIMIT 50
    """).fetchall()

    for sf in sf_changes:
        ts = sf['timestamp']
        nearby_td = db.execute("""
            SELECT msg_type, from_berth, to_berth, headcode
            FROM td_events
            WHERE ABS(julianday(timestamp) - julianday(?)) * 86400 < 30
            ORDER BY timestamp
            LIMIT 5
        """, (ts,)).fetchall()
        if nearby_td:
            t = ts[11:19]
            print(f"   SF {sf['address']} → {sf['new_val']} @ {t}")
            for td in nearby_td:
                print(f"      {td['msg_type']} {td['headcode']:>4s} {td['from_berth']:>4s}→{td['to_berth']}")

    db.close()
    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
