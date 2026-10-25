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

CATS = {0: "Unknown", 1: "Accident", 2: "Fog", 3: "Dangerous", 4: "Rain", 5: "Ice", 6: "Jam",
        7: "Lane closed", 8: "Road closed", 9: "Road works", 10: "Wind", 11: "Flooding",
        14: "Broken down"}


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
    import pandas as pd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if not FLOW_CSV.exists():
        sys.exit("No data yet - run 'collect' first.")
    STATS.mkdir(exist_ok=True)
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    f = pd.read_csv(FLOW_CSV)
    f["ts"] = pd.to_datetime(f["ts"], utc=True).dt.tz_convert(a.tz)
    f = f[f["free"] > 0].copy()
    f["cong"] = (1 - f["cur"] / f["free"]).clip(0, 1) * 100  # % slower than free flow
    f["hour"] = f["ts"].dt.hour
    f["dow"] = f["ts"].dt.dayofweek
    now = f["ts"].max()

    print(f"\nData: {f['ts'].min():%Y-%m-%d %H:%M} -> {now:%Y-%m-%d %H:%M} ({a.tz}), {len(f)} measurements")
    print("Congestion % = how much slower than free-flow speed (0 = free road).\n")

    print("=== SHORT TERM: last 24 h, average congestion % by hour ===")
    l24 = f[f["ts"] > now - pd.Timedelta(hours=24)]
    print(l24.groupby("hour")["cong"].mean().round(1).to_string())

    print("\n=== SHORT TERM: last 7 days, average congestion % per day ===")
    l7 = f[f["ts"] > now - pd.Timedelta(days=7)]
    print(l7.groupby(l7["ts"].dt.date)["cong"].mean().round(1).to_string())
    if len(l7):
        print(f"Worst hour of the last 7 days: {int(l7.groupby('hour')['cong'].mean().idxmax())}:00")

    print("\n=== LONG TERM: average congestion % by weekday x hour ===")
    pivot = f.pivot_table(index="dow", columns="hour", values="cong", aggfunc="mean")
    pivot.index = [days[i] for i in pivot.index]
    print(pivot.round(0).to_string())

    print("\n=== LONG TERM: monthly average congestion % ===")
    print(f.groupby(f["ts"].dt.strftime("%Y-%m"))["cong"].mean().round(1).to_string())

    print("\n=== Most congested measuring points (avg %) ===")
    print(f.groupby("point")["cong"].mean().sort_values(ascending=False).head(5).round(1).to_string())

    if INC_CSV.exists():
        inc = pd.read_csv(INC_CSV)
        inc["ts"] = pd.to_datetime(inc["ts"], utc=True).dt.tz_convert(a.tz)
        inc["cat"] = inc["cat"].map(CATS).fillna("Other")
        u = inc.groupby("id").agg(first_seen=("ts", "min"), cat=("cat", "first"),
                                  delay_s=("delay_s", "max"), roads=("roads", "first"))
        print("\n=== Incidents by type (unique) ===")
        print(u["cat"].value_counts().to_string())
        print("\n=== Incidents per day, last 14 days ===")
        print(u.groupby(u["first_seen"].dt.date).size().tail(14).to_string())
        print(f"\nTotal delay from incidents: {u['delay_s'].fillna(0).sum() / 3600:.1f} h")

    fig, ax = plt.subplots(figsize=(10, 4))
    im = ax.imshow(pivot.values, aspect="auto", cmap="RdYlGn_r")
    ax.set_xticks(range(len(pivot.columns)), pivot.columns)
    ax.set_yticks(range(len(pivot.index)), pivot.index)
    ax.set_title("Congestion % by weekday and hour")
    fig.colorbar(im)
    fig.tight_layout()
    fig.savefig(STATS / "heatmap.png", dpi=120)

    fig, ax = plt.subplots(figsize=(10, 4))
    l7.set_index("ts")["cong"].resample("1h").mean().plot(ax=ax)
    ax.set_title("Congestion %, last 7 days (hourly)")
    ax.set_ylabel("%")
    fig.tight_layout()
    fig.savefig(STATS / "last7days.png", dpi=120)
    print(f"\nCharts saved to {STATS}/")


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
    s.set_defaults(fn=cmd_stats)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
