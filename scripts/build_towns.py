#!/usr/bin/env python3
"""List the towns each day rides through, in order, with mile markers.

Separate from add_services.py because it answers a different question. That
script marks things you stop *at* and writes them into the GPX as waypoints;
this one describes the shape of the day - where you are at mile 20, where lunch
could plausibly be - and only ever reaches the web page.

Writes data/towns.json, keyed by day.

    python3 scripts/build_towns.py          # all days
    python3 scripts/build_towns.py 6        # just day 6
"""
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_data  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

OVERPASS = "https://overpass-api.de/api/interpreter"
USER_AGENT = "pacific-coast-2026/1.0 (bike tour route planning)"

# How far off the route a place can sit and still count as somewhere the day
# goes through. Wider than the POI radius: a town centre node is often a little
# off the road you actually ride.
MAX_OFFSET_MI = 2.0
BBOX_PAD_DEG = 0.02
TIMEOUT_S = 180
SLEEP_BETWEEN_DAYS_S = 12

# Ranked, so a city outranks a hamlet when both are close and we are thinning.
PLACE_RANK = {"city": 0, "town": 1, "village": 2, "hamlet": 3}
# Two places closer together than this along the route collapse to the better
# ranked one - otherwise contiguous suburbs read as a dozen separate stops.
MIN_SPACING_MI = 3.0

QUERY = """[out:json][timeout:180];
(
  node["place"~"^(city|town|village|hamlet)$"](%(bbox)s);
);
out body;"""


def haversine_mi(a, b):
    return build_data.haversine_m(a, b) * build_data.M_TO_MI


def overpass(bbox, attempts=6):
    q = QUERY % {"bbox": "%s,%s,%s,%s" % bbox}
    req = urllib.request.Request(
        OVERPASS,
        data=urllib.parse.urlencode({"data": q}).encode(),
        headers={"User-Agent": USER_AGENT},
    )
    delay = 20
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                return json.load(r)["elements"]
        except urllib.error.HTTPError as e:
            if e.code not in (429, 504, 502) or attempt == attempts:
                raise
            print(f"    overpass {e.code}, waiting {delay}s", flush=True)
            time.sleep(delay)
            delay = min(delay * 2, 300)
    raise RuntimeError("unreachable")


def towns_for(route):
    lats = [p[0] for p in route]
    lons = [p[1] for p in route]
    bbox = (min(lats) - BBOX_PAD_DEG, min(lons) - BBOX_PAD_DEG,
            max(lats) + BBOX_PAD_DEG, max(lons) + BBOX_PAD_DEG)

    cum = [0.0]
    for a, b in zip(route, route[1:]):
        cum.append(cum[-1] + haversine_mi(a, b))

    found = []
    for e in overpass(bbox):
        tags = e.get("tags", {})
        name = (tags.get("name") or "").strip()
        if not name:
            continue
        place = tags.get("place")
        dists = [haversine_mi((e["lat"], e["lon"]), (p[0], p[1])) for p in route]
        offset = min(dists)
        if offset > MAX_OFFSET_MI:
            continue
        i = dists.index(offset)
        found.append({
            "name": name, "place": place, "rank": PLACE_RANK.get(place, 9),
            "lat": e["lat"], "lon": e["lon"],
            "mile": round(cum[i], 1), "offset_mi": round(offset, 1),
        })

    # Collapse near-duplicates along the route, keeping the better-ranked one.
    found.sort(key=lambda t: (t["mile"], t["rank"]))
    kept = []
    for t in found:
        clash = next((k for k in kept if abs(k["mile"] - t["mile"]) < MIN_SPACING_MI), None)
        if clash is None:
            kept.append(t)
        elif t["rank"] < clash["rank"]:
            kept[kept.index(clash)] = t
    kept.sort(key=lambda t: t["mile"])
    return kept


def main():
    days = [int(a) for a in sys.argv[1:]] or list(range(1, 9))
    path = DATA_DIR / "towns.json"
    existing = json.loads(path.read_text()) if path.exists() else {}

    for day in days:
        route = json.loads((DATA_DIR / f"day-{day}.json").read_text())["route"]
        towns = towns_for(route)
        existing[str(day)] = towns
        path.write_text(json.dumps(existing, indent=1, sort_keys=True))
        names = ", ".join(t["name"] for t in towns)
        print(f"day {day}: {len(towns):>2} places  {names[:96]}")
        if day != days[-1]:
            time.sleep(SLEEP_BETWEEN_DAYS_S)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
