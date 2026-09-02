#!/usr/bin/env python3
"""Report every point on a day's track that sits on a motorway, a freeway ramp
or a trunk road, from OpenStreetMap via Overpass.

Deliberately narrow. OSM carries no shoulder or width tag on most of these
roads, so this cannot tell you a road is safe — only that a road is one of the
few kinds that are definitely not, or that carry traffic fast enough to be
worth knowing about before you are on it.

    python3 scripts/check_roads.py            # every day
    python3 scripts/check_roads.py 7 8        # just those

One Overpass query per day asks for motorway and trunk ways near the route; a
second, only if that found something, asks what else is near those few points.
Matching is local. Slower than check_gpx.py and dependent on a free shared
service, so run it when a route changes rather than on every build.

WHY IT REPORTS BIKE PATHS TOO. An earlier version excluded cycleways from
matching, on the theory that only roads mattered. Days 1, 2 and 3 promptly
reported motorway: the route runs on the Los Gatos Creek Trail and the Monterey
Bay Coastal Trail, both of which sit ten to twenty metres from a freeway, so
with the path filtered out the nearest remaining way was the freeway itself.
It reported half a mile of 65 mph motorway that nobody rides on. Every nearby
way is now considered and the closest wins, which is why a hit prints what else
was in range: on this route, that column is usually the answer.

TRUNK IS NOT AN ERROR. Pacific Coast Highway, CA-1 and Foothill Expressway are
trunk-class and are the route — they are reported so the list is complete, not
because anything is wrong. Motorway is the class worth reading twice, and even
there California signs some freeway shoulders as legal for bicycles: OSM
records US-101 through Gaviota as bicycle=yes with a shoulder. Whether the
signs are actually up is not something this script can see.
"""
import json
import math
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
GPX_DIR = ROOT / "gpx"
NS = "{http://www.topografix.com/GPX/1/1}"
OVERPASS = "https://overpass-api.de/api/interpreter"
USER_AGENT = "pacific-coast-2026/1.0 (bike tour route planning)"

FAST_CLASSES = ("motorway", "motorway_link", "trunk", "trunk_link")
# How far apart to sample. Fine enough to catch a ramp crossing, coarse enough
# to keep the Overpass query to one request per day.
SAMPLE_MI = 0.15
# Fetch anything within this of a sample, then decide locally.
FETCH_RADIUS_M = 40
# A track point this close to a way is on it, allowing for how loosely a
# hand-drawn line follows a centreline.
ON_ROAD_M = 12.0
TIMEOUT_S = 300
SLEEP_BETWEEN_DAYS_S = 5


def haversine_m(a, b):
    r = 6371000.0
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(h))


def overpass(query, attempts=5):
    """Overpass is free, shared and rate-limited; 429 and 504 are routine."""
    for i in range(attempts):
        try:
            request = urllib.request.Request(
                OVERPASS, data=urllib.parse.urlencode({"data": query}).encode(),
                headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                return json.load(response)["elements"]
        except Exception as e:
            print(f"    overpass retry {i + 1}/{attempts}: {e}", file=sys.stderr)
            time.sleep(20)
    return None


def sample(path, step_mi):
    pts = [(float(p.get("lat")), float(p.get("lon")))
           for p in ET.parse(path).getroot().iter(NS + "trkpt")]
    out, since, mile = [(0.0, pts[0])], 0.0, 0.0
    for a, b in zip(pts, pts[1:]):
        d = haversine_m(a, b) / 1609.344
        mile += d
        since += d
        if since >= step_mi:
            out.append((round(mile, 2), b))
            since = 0.0
    return out, mile


def nearest(point, ways):
    best = None
    for w in ways:
        geom = w.get("geometry") or []
        if not geom:
            continue
        d = min(haversine_m(point, (g["lat"], g["lon"])) for g in geom)
        if best is None or d < best[0]:
            best = (d, w["tags"])
    return best


def check_day(day, path):
    samples, miles = sample(path, SAMPLE_MI)
    coords = ",".join(f"{la},{lo}" for _, (la, lo) in samples)
    fast = overpass(f'[out:json][timeout:{TIMEOUT_S}];'
                    f'way(around:{FETCH_RADIUS_M},{coords})'
                    f'["highway"~"^({"|".join(FAST_CLASSES)})$"];out tags geom;')
    if fast is None:
        print(f"day {day}: OVERPASS UNAVAILABLE — not audited")
        return None
    if not fast:
        print(f"day {day}: clean — nothing fast within {FETCH_RADIUS_M} m of "
              f"{len(samples)} samples over {miles:.1f} mi")
        return []

    # Candidates: samples that sit on something fast. Most days have none, and
    # the ones that do have a handful — which is the point of narrowing before
    # the second query. Asking for every other road along the whole route
    # instead takes minutes and returns thousands of ways to answer a question
    # about six points.
    candidates = []
    for mile, point in samples:
        near_fast = nearest(point, fast)
        if near_fast and near_fast[0] <= ON_ROAD_M:
            candidates.append((mile, near_fast, point))
    if not candidates:
        print(f"day {day}: clean — {len(samples)} samples over {miles:.1f} mi, "
              f"nothing within {ON_ROAD_M:.0f} m of a fast road")
        return []

    near_coords = ",".join(f"{p[0]},{p[1]}" for _, _, p in candidates)
    other = overpass(f'[out:json][timeout:{TIMEOUT_S}];'
                     f'way(around:{FETCH_RADIUS_M},{near_coords})'
                     f'["highway"]["highway"!~"^({"|".join(FAST_CLASSES)})$"];'
                     f'out tags geom;') or []

    hits = []
    for mile, near_fast, point in candidates:
        near_other = nearest(point, other)
        # If a path or a street is closer than the freeway, that is where the
        # rider is. This is the check that stopped bike paths reading as 101.
        if near_other and near_other[0] < near_fast[0]:
            continue
        hits.append((mile, near_fast, near_other, point))

    runs = []
    for h in hits:
        if runs and h[0] - runs[-1][-1][0] <= SAMPLE_MI * 2.5:
            runs[-1].append(h)
        else:
            runs.append([h])
    print(f"day {day}: {len(hits)} of {len(samples)} samples on a fast road")
    for run in runs:
        (_, (dist, tags), other, point) = run[0]
        alt = (f", nearest other way {other[0]:.0f} m ({other[1].get('highway')})"
               if other else "")
        bike = tags.get("bicycle")
        note = f", bicycle={bike}" if bike else ""
        print(f"   mi {run[0][0]:6.2f}–{run[-1][0]:6.2f}  "
              f"{tags.get('highway'):14s} {tags.get('name') or '(unnamed)'}"
              f"  [{dist:.0f} m{note}{alt}]  at {point[0]:.5f},{point[1]:.5f}")
    return hits


def main():
    import build_data
    days = [int(a) for a in sys.argv[1:]] or sorted(build_data.GPX_FILES)
    for i, n in enumerate(days):
        check_day(n, GPX_DIR / build_data.GPX_FILES[n])
        if i + 1 < len(days):
            time.sleep(SLEEP_BETWEEN_DAYS_S)
    print("\nNothing here is verified on the ground. A road absent from this "
          "list is not thereby safe;\nOpenStreetMap has no shoulder or width "
          "tag on most of them.")


if __name__ == "__main__":
    main()
