#!/usr/bin/env python3
"""
Find TD berths near Roundstone Level Crossing from SMART data.

Parses the downloaded SMART dataset to find berths in the "ES" TD area
that are geographically near the crossing (between Angmering and Goring-by-Sea).

Usage:
    python scripts/find_berths.py

Requires: data/smart.json (run download_reference_data.py first)
"""

import json
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SMART_PATH = DATA_DIR / "smart.json"
CORPUS_PATH = DATA_DIR / "corpus.json"

# TD area for Sussex Coastway
TARGET_TD_AREA = "ES"

# STANOXes for stations either side of the crossing
STANOX_ANGMERING = "87998"
STANOX_GORING = "87997"

# TIPLOCs of interest
TIPLOCS_OF_INTEREST = {"ANGMRNG", "GORNGBS"}


def load_smart():
    """Load and return SMART berth data."""
    if not SMART_PATH.exists():
        print(f"ERROR: {SMART_PATH} not found. Run download_reference_data.py first.")
        sys.exit(1)

    data = json.loads(SMART_PATH.read_text())

    # SMART data structure varies — handle both formats
    if "BERTHDATA" in data:
        return data["BERTHDATA"]
    elif "SmartExtract" in data:
        return data["SmartExtract"]
    elif isinstance(data, list):
        return data
    else:
        # Try to find the berth array in nested structure
        for key, val in data.items():
            if isinstance(val, list) and len(val) > 0:
                return val
            if isinstance(val, dict):
                for k2, v2 in val.items():
                    if isinstance(v2, list) and len(v2) > 0:
                        return v2
        print(f"ERROR: Unrecognised SMART data structure. Top-level keys: {list(data.keys())}")
        sys.exit(1)


def load_corpus():
    """Load CORPUS data for STANOX/TIPLOC cross-reference."""
    if not CORPUS_PATH.exists():
        return None

    data = json.loads(CORPUS_PATH.read_text())

    if "TIPLOCDATA" in data:
        return data["TIPLOCDATA"]
    elif "CORPUSExtract" in data:
        return data["CORPUSExtract"]
    elif isinstance(data, list):
        return data
    else:
        for key, val in data.items():
            if isinstance(val, list) and len(val) > 0:
                return val
            if isinstance(val, dict):
                for k2, v2 in val.items():
                    if isinstance(v2, list) and len(v2) > 0:
                        return v2
        return None


def find_es_berths(smart_data):
    """Find all berths in the ES TD area."""
    es_berths = []
    for entry in smart_data:
        td_area = entry.get("TD", "") or entry.get("td", "") or entry.get("FROMBERTH", "")[:2] if len(entry.get("FROMBERTH", "")) >= 2 else ""
        # Check various field name conventions
        area = (
            entry.get("TD", "")
            or entry.get("td", "")
            or entry.get("AREA", "")
            or entry.get("area", "")
            or entry.get("td_area", "")
            or ""
        )
        if area.strip().upper() == TARGET_TD_AREA:
            es_berths.append(entry)
    return es_berths


def find_relevant_berths(es_berths):
    """Filter ES berths to those likely near the crossing (between Angmering and Goring)."""
    relevant = []
    for entry in es_berths:
        # Check STANOX associations
        stanox = str(entry.get("STANOX", "") or entry.get("stanox", "") or "").strip()
        steptype = entry.get("STEPTYPE", "") or entry.get("steptype", "") or ""
        from_berth = entry.get("FROMBERTH", "") or entry.get("fromberth", "") or entry.get("FROM_BERTH", "") or ""
        to_berth = entry.get("TOBERTH", "") or entry.get("toberth", "") or entry.get("TO_BERTH", "") or ""
        event = entry.get("EVENT", "") or entry.get("event", "") or ""
        route = entry.get("ROUTE", "") or entry.get("route", "") or ""
        tiploc = entry.get("TIPLOC", "") or entry.get("tiploc", "") or ""

        # Include berths associated with Angmering or Goring STANOXes
        if stanox in (STANOX_ANGMERING, STANOX_GORING) or tiploc in TIPLOCS_OF_INTEREST:
            relevant.append({
                "from": from_berth.strip(),
                "to": to_berth.strip(),
                "stanox": stanox,
                "tiploc": tiploc,
                "steptype": steptype.strip(),
                "event": event.strip(),
                "route": route.strip(),
                "raw": entry,
            })

    return relevant


def main():
    print("=" * 70)
    print("SMART Berth Finder — Roundstone Level Crossing")
    print("=" * 70)

    # Load SMART data
    print(f"\nLoading SMART data from {SMART_PATH}...")
    smart_data = load_smart()
    print(f"  Total SMART entries: {len(smart_data)}")

    # Show a sample entry to understand structure
    if smart_data:
        print(f"\n  Sample entry keys: {list(smart_data[0].keys())}")

    # Find ES area berths
    es_berths = find_es_berths(smart_data)
    print(f"\n  ES area berths: {len(es_berths)}")

    if not es_berths:
        print("\n  No ES-area berths found. Dumping all unique TD areas:")
        areas = set()
        for entry in smart_data:
            area = (
                entry.get("TD", "")
                or entry.get("td", "")
                or entry.get("AREA", "")
                or entry.get("area", "")
                or ""
            ).strip()
            if area:
                areas.add(area)
        for area in sorted(areas):
            print(f"    {area}")
        print("\n  Check which area covers Sussex Coastway and update TARGET_TD_AREA.")
        return

    # Find berths near the crossing
    relevant = find_relevant_berths(es_berths)

    print(f"\n{'=' * 70}")
    print(f"Berths associated with Angmering (STANOX {STANOX_ANGMERING}) or Goring (STANOX {STANOX_GORING}):")
    print(f"{'=' * 70}")

    if not relevant:
        print("  No berths matched by STANOX. Listing all ES berths instead:\n")
        # Fall back to showing all ES berths with their STANOXes
        seen_stanox = set()
        for entry in es_berths:
            stanox = str(entry.get("STANOX", "") or "").strip()
            if stanox:
                seen_stanox.add(stanox)
        print(f"  Unique STANOXes in ES area: {sorted(seen_stanox)}")
        print(f"\n  Listing first 30 ES berth steps:")
        for i, entry in enumerate(es_berths[:30]):
            from_b = entry.get("FROMBERTH", "") or entry.get("FROM_BERTH", "")
            to_b = entry.get("TOBERTH", "") or entry.get("TO_BERTH", "")
            stanox = entry.get("STANOX", "")
            event = entry.get("EVENT", "")
            print(f"    {from_b:>8} → {to_b:<8}  STANOX={stanox}  EVENT={event}")
    else:
        # Group by direction (infer from berth number patterns)
        print(f"\n  Found {len(relevant)} berth steps:\n")
        print(f"  {'FROM':>8}  {'TO':>8}  {'STANOX':>8}  {'TIPLOC':>10}  {'STEP':>6}  {'EVENT':>8}  {'ROUTE':>6}")
        print(f"  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*10}  {'-'*6}  {'-'*8}  {'-'*6}")
        for r in sorted(relevant, key=lambda x: (x["stanox"], x["from"])):
            print(f"  {r['from']:>8}  {r['to']:>8}  {r['stanox']:>8}  {r['tiploc']:>10}  {r['steptype']:>6}  {r['event']:>8}  {r['route']:>6}")

    # Also show berths NOT matched but close in number range
    all_berth_ids = set()
    for entry in es_berths:
        for key in ["FROMBERTH", "TOBERTH", "FROM_BERTH", "TO_BERTH", "fromberth", "toberth"]:
            val = str(entry.get(key, "")).strip()
            if val:
                all_berth_ids.add(val)

    print(f"\n{'=' * 70}")
    print(f"All unique berth IDs in ES area ({len(all_berth_ids)} total):")
    print(f"{'=' * 70}")
    for berth in sorted(all_berth_ids):
        print(f"  {berth}")

    # CORPUS cross-reference
    corpus = load_corpus()
    if corpus:
        print(f"\n{'=' * 70}")
        print("CORPUS entries for stations near crossing:")
        print(f"{'=' * 70}")
        for entry in corpus:
            tiploc = (entry.get("TIPLOC", "") or entry.get("tiploc", "") or "").strip()
            stanox = str(entry.get("STANOX", "") or entry.get("stanox", "") or "").strip()
            nlcdesc = entry.get("NLCDESC", "") or entry.get("nlcdesc", "") or ""
            if tiploc in TIPLOCS_OF_INTEREST or stanox in (STANOX_ANGMERING, STANOX_GORING):
                print(f"  TIPLOC={tiploc}  STANOX={stanox}  DESC={nlcdesc}")
            # Also look for anything mentioning Angmering or Goring
            desc_upper = nlcdesc.upper()
            if "ANGMER" in desc_upper or "GORING" in desc_upper or "ROUNDSTONE" in desc_upper:
                print(f"  TIPLOC={tiploc}  STANOX={stanox}  DESC={nlcdesc}")


if __name__ == "__main__":
    main()
