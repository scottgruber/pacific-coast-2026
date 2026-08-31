#!/usr/bin/env python3
"""Add drinking-water and toilet waypoints to the day GPX tracks, from
OpenStreetMap via the Overpass API.

The point is the head unit: a Garmin or Wahoo shows these as waypoints along
the route, which is what actually helps on the road. build_data.py also reads
them back out so the day pages can list them with mile markers.

Idempotent — service waypoints are tagged with a <type> from SERVICE_TYPES and
any existing ones are stripped before new ones are written, so re-running
refreshes rather than duplicates. Turn cues and lodging waypoints already in a
file are left alone.

Run sparingly: Overpass is a free, shared, rate-limited service.

    python3 scripts/add_services.py            # all days
    python3 scripts/add_services.py 4 5        # just those days

COVERAGE IS NOT COMPLETENESS. OSM is volunteer-mapped and thin in rural
California — the inland days have long stretches with nothing mapped. A gap
here means "nobody has mapped anything", not "there is no water", and a
waypoint means "someone mapped a tap once", not "it is running today".
"""
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GPX_DIR = ROOT / "gpx"
DATA_DIR = ROOT / "data"

NS_URI = "http://www.topografix.com/GPX/1/1"
NS = "{%s}" % NS_URI

OVERPASS = "https://overpass-api.de/api/interpreter"
USER_AGENT = "pacific-coast-2026/1.0 (bike tour route planning)"

# How far off the track a facility can be and still be worth stopping for.
MAX_OFFSET_MI = 0.25
# Two taps 30 m apart are one stop; collapse them.
DEDUPE_MI = 0.05
BBOX_PAD_DEG = 0.02
TIMEOUT_S = 120
# Overpass is free and shared. Be a good citizen between days.
SLEEP_BETWEEN_DAYS_S = 12

# What to look for, and how each is presented. Ordered by resupply value
# within a category: when several fall close together only the best survives
# thinning, so a supermarket beats a corner shop and a shop beats a cafe.
#
# `sym` values are Garmin's own symbol names so the head unit draws a real icon
# rather than a generic pin. `spacing_mi` is the minimum gap between kept
# points of that type - the whole POI set competes with turn cues for a
# Garmin course's capped course-point budget, so density matters more than
# completeness. Water is never thinned: it is the scarce thing.
CATEGORIES = {
    ("amenity", "drinking_water"): dict(type="Water", sym="Drinking Water",
                                        priority=0, spacing_mi=0.0),
    ("amenity", "toilets"):        dict(type="Toilets", sym="Restroom",
                                        priority=0, spacing_mi=2.0),
    ("shop", "supermarket"):       dict(type="Store", sym="Shopping Center",
                                        priority=0, spacing_mi=1.5),
    ("shop", "convenience"):       dict(type="Store", sym="Convenience Store",
                                        priority=1, spacing_mi=1.5),
    ("shop", "deli"):              dict(type="Store", sym="Convenience Store",
                                        priority=2, spacing_mi=1.5),
    ("amenity", "cafe"):           dict(type="Food", sym="Restaurant",
                                        priority=0, spacing_mi=2.5),
    ("amenity", "restaurant"):     dict(type="Food", sym="Restaurant",
                                        priority=1, spacing_mi=2.5),
    ("amenity", "fast_food"):      dict(type="Food", sym="Fast Food",
                                        priority=2, spacing_mi=2.5),
    # Worth stopping for rather than worth relying on, so spaced widely and
    # only kept when named - an unnamed viewpoint tells a rider nothing.
    ("tourism", "viewpoint"):      dict(type="Scenic", sym="Scenic Area",
                                        priority=0, spacing_mi=3.0, named_only=True),
    ("tourism", "attraction"):     dict(type="Scenic", sym="Scenic Area",
                                        priority=1, spacing_mi=3.0, named_only=True),
    ("tourism", "museum"):         dict(type="Historic", sym="Museum",
                                        priority=0, spacing_mi=3.0, named_only=True),
    ("historic", "monument"):      dict(type="Historic", sym="Museum",
                                        priority=1, spacing_mi=3.0, named_only=True),
    ("historic", "memorial"):      dict(type="Historic", sym="Museum",
                                        priority=2, spacing_mi=3.0, named_only=True),
    ("historic", "ruins"):         dict(type="Historic", sym="Museum",
                                        priority=1, spacing_mi=3.0, named_only=True),
    ("historic", "building"):      dict(type="Historic", sym="Museum",
                                        priority=3, spacing_mi=3.0, named_only=True),
}

# amenity=toilets says a toilet exists, not that a passing cyclist may use it.
# Auditing the route turned up private units, permit-only beach facilities, and
# six separate toilet nodes inside Santa Barbara City College - none of them
# any use on a ride, and a waypoint that sends someone onto a school campus is
# worse than no waypoint at all.
#
# access=customers is kept and labelled: buying a coffee is a fair trade, and
# in practice a cafe is the most reliable restroom on any of these routes.
BLOCKED_ACCESS = {"private", "no", "permit", "key", "military", "delivery"}
CUSTOMER_ACCESS = {"customers", "customer"}
# Matched against name and operator. A toilet run by one of these is on
# somebody's campus, not by the road.
BLOCKED_OPERATOR_WORDS = ("school", "college", "university", "academy",
                          "church", "private", "country club")

SERVICE_TYPES = {k: v["type"] for k, v in CATEGORIES.items()}
ALL_TYPES = sorted({v["type"] for v in CATEGORIES.values()})

QUERY = """[out:json][timeout:120];
(
  node["amenity"~"^(drinking_water|toilets|cafe|restaurant|fast_food)$"](%(bbox)s);
  way["amenity"="toilets"](%(bbox)s);
  node["shop"~"^(supermarket|convenience|deli)$"](%(bbox)s);
  way["shop"~"^(supermarket|convenience)$"](%(bbox)s);
  node["tourism"~"^(viewpoint|attraction|museum)$"](%(bbox)s);
  node["historic"~"^(monument|memorial|ruins|building)$"](%(bbox)s);
  way["historic"~"^(monument|memorial|ruins|building)$"](%(bbox)s);
);
out center tags;"""


def haversine_mi(a, b):
    r = 3958.7613
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = p2 - p1
    dl = math.radians(b[1] - a[1])
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(x))


def overpass(bbox, attempts=5):
    """Query Overpass, backing off on 429/504. It is a free shared service and
    will refuse a burst; the wait is expected, not an error."""
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
            if e.code not in (429, 504) or attempt == attempts:
                raise
            print(f"    overpass {e.code}, waiting {delay}s "
                  f"(attempt {attempt}/{attempts})", flush=True)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def route_of(day):
    """The simplified polyline from data/day-N.json — accurate enough to
    measure offsets and mile markers, and far cheaper than the raw track."""
    return json.loads((DATA_DIR / f"day-{day}.json").read_text())["route"]


def categorise(tags):
    """The CATEGORIES entry matching an element's tags, or None."""
    for (k, v), cfg in CATEGORIES.items():
        if tags.get(k) == v:
            return cfg
    return None


def find_services(route):
    """Facilities within MAX_OFFSET_MI of the route, each with the mile along
    the route where it is closest, thinned per category."""
    lats = [p[0] for p in route]
    lons = [p[1] for p in route]
    bbox = (min(lats) - BBOX_PAD_DEG, min(lons) - BBOX_PAD_DEG,
            max(lats) + BBOX_PAD_DEG, max(lons) + BBOX_PAD_DEG)

    cum = [0.0]
    for a, b in zip(route, route[1:]):
        cum.append(cum[-1] + haversine_mi(a, b))

    found = []
    for e in overpass(bbox):
        lat = e.get("lat") or (e.get("center") or {}).get("lat")
        lon = e.get("lon") or (e.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        tags = e.get("tags", {})
        cfg = categorise(tags)
        if cfg is None:
            continue
        name = (tags.get("name") or "").strip()
        if cfg.get("named_only") and not name:
            continue

        # Access filtering, for toilets above all.
        access = (tags.get("access") or "").strip().lower()
        if access in BLOCKED_ACCESS:
            continue
        blob = f"{name} {tags.get('operator', '')}".lower()
        if any(w in blob for w in BLOCKED_OPERATOR_WORDS):
            continue
        customers_only = access in CUSTOMER_ACCESS
        fee = (tags.get("fee") or "").strip().lower() == "yes"
        dists = [haversine_mi((lat, lon), (p[0], p[1])) for p in route]
        offset_mi = min(dists)
        if offset_mi > MAX_OFFSET_MI:
            continue
        i = dists.index(offset_mi)
        found.append({
            "lat": lat, "lon": lon, "name": name,
            "type": cfg["type"], "sym": cfg["sym"],
            "customers_only": customers_only, "fee": fee,
            "priority": cfg["priority"], "spacing_mi": cfg["spacing_mi"],
            "mile": round(cum[i], 1), "offset_mi": round(offset_mi, 2),
        })

    # Thin per category: walking in mile order, drop anything closer than its
    # own spacing to one already kept. Sorting by priority first means the most
    # useful member of a cluster is the one that survives - a supermarket
    # rather than the fast-food place next door. Water is never thinned.
    found.sort(key=lambda s: (s["mile"], s["priority"]))
    kept = []
    for s in found:
        if s["spacing_mi"] > 0 and any(
            k["type"] == s["type"] and abs(k["mile"] - s["mile"]) < s["spacing_mi"]
            for k in kept
        ):
            continue
        if any(k["type"] == s["type"]
               and haversine_mi((s["lat"], s["lon"]), (k["lat"], k["lon"])) < DEDUPE_MI
               for k in kept):
            continue
        kept.append(s)
    kept.sort(key=lambda s: s["mile"])
    return kept


def write_waypoints(path, services):
    ET.register_namespace("", NS_URI)
    tree = ET.parse(path)
    root = tree.getroot()

    # Drop service waypoints from a previous run so this is a refresh, not an
    # append. Anything else in the file (turn cues, lodging) is left as is.
    for wpt in list(root.findall(NS + "wpt")):
        if (wpt.findtext(NS + "type") or "") in ALL_TYPES:
            root.remove(wpt)

    # GPX requires wpt elements before trk, so insert ahead of the first one.
    trk = root.find(NS + "trk")
    insert_at = list(root).index(trk) if trk is not None else len(list(root))

    for s in services:
        kind = s["type"]
        wpt = ET.Element(NS + "wpt", {"lat": f"{s['lat']:.6f}", "lon": f"{s['lon']:.6f}"})
        label = s["name"] or kind
        ET.SubElement(wpt, NS + "name").text = f"{label} (mi {s['mile']:.1f})"
        off = f", {s['offset_mi']:.2f} mi off route" if s["offset_mi"] >= 0.02 else ""
        notes = ""
        if s.get("customers_only"):
            notes += " Customers only."
        if s.get("fee"):
            notes += " Fee."
        ET.SubElement(wpt, NS + "cmt").text = (
            f"{kind} at mile {s['mile']:.1f}{off}.{notes} Mapped in "
            f"OpenStreetMap; not verified on the ground."
        )
        ET.SubElement(wpt, NS + "desc").text = kind
        ET.SubElement(wpt, NS + "sym").text = s["sym"]
        ET.SubElement(wpt, NS + "type").text = kind
        root.insert(insert_at, wpt)
        insert_at += 1

    tree.write(path, encoding="UTF-8", xml_declaration=True)


def gpx_files_for(day):
    """Every candidate track for a day, primary and alternates alike."""
    import build_data
    files = {build_data.GPX_FILES[day]}
    files.update(o["file"] for o in build_data.ROUTE_OPTIONS.get(day, []))
    return sorted(files)


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    days = [int(a) for a in sys.argv[1:]] or list(range(1, 9))

    for day in days:
        route = route_of(day)
        services = find_services(route)
        counts = {}
        for s in services:
            counts[s["type"]] = counts.get(s["type"], 0) + 1

        # The number that actually matters for planning: the longest stretch
        # with no mapped water, start and finish included as known stops.
        water_miles = [0.0] + [s["mile"] for s in services
                               if s["type"] == "Water"]
        total = sum(haversine_mi(a, b) for a, b in zip(route, route[1:]))
        water_miles.append(total)
        gap = max(b - a for a, b in zip(water_miles, water_miles[1:]))

        for name in gpx_files_for(day):
            write_waypoints(GPX_DIR / name, services)

        summary = "  ".join(f"{counts.get(t, 0):>3} {t.lower()}" for t in ALL_TYPES)
        print(f"day {day}: {summary}   dry stretch {gap:5.1f} mi")
        if day != days[-1]:
            time.sleep(SLEEP_BETWEEN_DAYS_S)


if __name__ == "__main__":
    main()
