#!/usr/bin/env python3
"""
Bratislava live traffic tracker (TomTom Traffic API).

  python traffic.py collect [URL] [--radius 10] [--grid 3] [--loop 15] [--push]
  python traffic.py stats [--tz Europe/Bratislava]

API key: environment variable TOMTOM_KEY (free key at developer.tomtom.com).
Data: data/flow.csv (speeds per grid point), data/incidents.csv (jams, closures, ...).
"""
import argparse
import csv
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from math import cos, radians
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests

def load_env(path=".env"):
    """Read KEY=value lines from a local .env file (never committed, see .gitignore)."""
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


DEFAULT_URL = ("https://plan.tomtom.com/en?p=48.1323,17.14649,10.18z"
               "#mode=viewport+viewport=48.07023,17.92671,6.61,0,-0+ver=3")
DATA = Path("data")
STATS = Path("stats")
FLOW_CSV = DATA / "flow.csv"
INC_CSV = DATA / "incidents.csv"

FLOW_FIELDS = ["ts", "point", "lat", "lon", "cur", "free", "cur_tt", "free_tt", "confidence", "closed"]
INC_FIELDS = ["ts", "id", "cat", "magnitude", "delay_s", "length_m", "from", "to", "roads", "start"]


# ---------- collecting ----------

def parse_url(url):
    """Map centre (lat, lon) from the p=lat,lon,zoomz parameter."""
    q = parse_qs(urlparse(url).query)
    m = re.match(r"\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)", unquote(q["p"][0]))
    if not m:
        sys.exit("Could not read coordinates from the link (missing p=lat,lon,zoom).")
    return float(m[1]), float(m[2])


def append_rows(path, fields, rows):
    DATA.mkdir(exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(fields)
        w.writerows(rows)


def fetch_flow(key, lat, lon):
    url = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"
    r = requests.get(url, params={"key": key, "point": f"{lat},{lon}", "unit": "KMPH"}, timeout=20)
    if r.status_code != 200:
        print(f"  flow {lat:.4f},{lon:.4f}: HTTP {r.status_code}", file=sys.stderr)
        return None
    return r.json()["flowSegmentData"]


def fetch_incidents(key, bbox):
    url = "https://api.tomtom.com/traffic/services/5/incidentDetails"
    fields = ("{incidents{type,properties{id,iconCategory,magnitudeOfDelay,delay,length,"
              "from,to,roadNumbers,startTime}}}")
    r = requests.get(url, params={
        "key": key, "bbox": ",".join(f"{v:.5f}" for v in bbox), "fields": fields,
        "language": "en-GB", "timeValidityFilter": "present"}, timeout=30)
    if r.status_code != 200:
        print(f"  incidents: HTTP {r.status_code} {r.text[:150]}", file=sys.stderr)
        return []
    return r.json().get("incidents", [])


def collect_once(key, lat, lon, radius_km, grid):
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    dlat = radius_km / 111.0
    dlon = radius_km / (111.32 * cos(radians(lat)))
    steps = [0.0] if grid == 1 else [-1 + 2 * i / (grid - 1) for i in range(grid)]

    flow_rows = []
    for i, sy in enumerate(steps):
        for j, sx in enumerate(steps):
            plat, plon = lat + sy * dlat, lon + sx * dlon
            d = fetch_flow(key, plat, plon)
            if d:
                flow_rows.append([ts, f"{i}-{j}", round(plat, 5), round(plon, 5),
                                  d.get("currentSpeed"), d.get("freeFlowSpeed"),
                                  d.get("currentTravelTime"), d.get("freeFlowTravelTime"),
                                  d.get("confidence"), d.get("roadClosure", False)])
    append_rows(FLOW_CSV, FLOW_FIELDS, flow_rows)

    bbox = (lon - dlon, lat - dlat, lon + dlon, lat + dlat)  # minLon,minLat,maxLon,maxLat
    inc_rows = []
    for inc in fetch_incidents(key, bbox):
        p = inc.get("properties", {})
        inc_rows.append([ts, p.get("id"), p.get("iconCategory"), p.get("magnitudeOfDelay"),
                         p.get("delay"), p.get("length"), p.get("from"), p.get("to"),
                         ",".join(p.get("roadNumbers") or []), p.get("startTime")])
    if inc_rows:
        append_rows(INC_CSV, INC_FIELDS, inc_rows)
    print(f"{ts}  flow points: {len(flow_rows)}  incidents: {len(inc_rows)}")


def git_push():
    subprocess.run(["git", "add", "data"], check=False)
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode != 0:
        subprocess.run(["git", "commit", "-m", "traffic data"], check=False)
        subprocess.run(["git", "push"], check=False)


def cmd_collect(a):
    load_env()
    key = os.environ.get("TOMTOM_KEY")
    if not key:
        sys.exit("Put TOMTOM_KEY=... into the .env file (or set the environment variable).")
    lat, lon = parse_url(a.url)
    print(f"Centre: {lat}, {lon}  radius: {a.radius} km  grid: {a.grid}x{a.grid}")
    while True:
        collect_once(key, lat, lon, a.radius, a.grid)
        if a.push:
            git_push()
        if not a.loop:
            break
        time.sleep(a.loop * 60)


# ---------- statistics ----------

def cmd_stats(a):
    from report import run_report
    run_report(a.tz, a.out, a.html, FLOW_CSV, INC_CSV)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="download traffic data for the area in the link")
    c.add_argument("url", nargs="?", default=DEFAULT_URL,
                   help="plan.tomtom.com link (default: Bratislava)")
    c.add_argument("--radius", type=float, default=10, help="half-size of the area in km (default 10)")
    c.add_argument("--grid", type=int, default=3, help="NxN measuring points (default 3 -> 9 points)")
    c.add_argument("--loop", type=float, default=0, help="repeat every N minutes (0 = run once)")
    c.add_argument("--push", action="store_true", help="git commit + push data after each run")
    c.set_defaults(fn=cmd_collect)

    s = sub.add_parser("stats", help="print short/long term statistics and save charts")
    s.add_argument("--tz", default="Europe/Bratislava")
    s.add_argument("--out", default="stats", help="output folder for charts")
    s.add_argument("--html", action="store_true", help="also write index.html (for GitHub Pages)")
    s.set_defaults(fn=cmd_stats)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
