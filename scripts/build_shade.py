#!/usr/bin/env python3
"""Estimate how shaded each day's route is in the afternoon.

Answers the question the GPX cannot: on a hot inland day, how much of this
route is actually in shade when it matters? Two independent sources of shade,
computed separately and reported separately:

  TERRAIN  A rider is in terrain shade when something between them and the sun
           rises above the sun's own elevation angle. This samples the ground
           along the sun's compass bearing at increasing distances and takes
           the largest angle subtended - the classic horizon-angle test. It is
           the honest version of "aspect": a west-facing slope only helps in
           late afternoon if it is steep enough and close enough to block the
           sun, which a bare aspect number never tells you.

  CANOPY   Whether the point falls inside mapped woodland (OpenStreetMap
           natural=wood / landuse=forest / natural=scrub).

Neither needs a paid key or a new dependency:
  - elevation from Open-Meteo's elevation API (free, no key, 100 points/call)
  - woodland from Overpass
  - solar position from the NOAA algorithm, implemented here

Writes data/shade.json, keyed by day. Kept out of data/day-N.json because
build_data.py rewrites those wholesale and this is far too slow to run on
every build.

    python3 scripts/build_shade.py           # all days
    python3 scripts/build_shade.py 3         # just day 3
    python3 scripts/build_shade.py 3 --hour 15

CAVEATS. Canopy is only as good as OSM's mapping, which is thin inland - an
unshaded result there may mean "unmapped", not "treeless". Terrain shade uses
a coarse elevation model and ignores clouds and the rider's own timing; it
says where shade is possible at one instant, not where you will find it.
"""
import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_data  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

ELEVATION_API = "https://api.open-meteo.com/v1/elevation"
OVERPASS = "https://overpass-api.de/api/interpreter"
USER_AGENT = "pacific-coast-2026/1.0 (bike tour shade analysis)"

SAMPLE_INTERVAL_MI = 1.0
# Distances along the sun bearing at which to test for a blocking horizon.
# Near samples catch roadside banks and cuttings; far ones catch ridgelines.
HORIZON_DISTANCES_M = [100, 200, 400, 800, 1600, 3200]
ELEVATION_BATCH = 100
# Terrain does not change; elevations are cached so re-runs cost nothing.
ELEVATION_CACHE = ROOT / "data" / ".elevation-cache.json"
# Woodland this close to the road shades it. Roughly two mature tree heights,
# which is about how far a canopy throws shade with the sun well off vertical.
CANOPY_WITHIN_M = 30
DEFAULT_HOUR_LOCAL = 15  # 3pm - the hottest, most exposed part of a riding day
PACIFIC_UTC_OFFSET_H = -7  # PDT, which is what September is on

WOOD_QUERY = """[out:json][timeout:120];
(
  way["natural"="wood"](%(bbox)s);
  way["landuse"="forest"](%(bbox)s);
  way["natural"="scrub"](%(bbox)s);
);
out geom;"""


# --- geometry -------------------------------------------------------------

def haversine_mi(a, b):
    return build_data.haversine_m(a, b) * build_data.M_TO_MI


def offset(lat, lon, bearing_deg, distance_m):
    """Point `distance_m` from (lat, lon) along a compass bearing."""
    r = 6371000.0
    br = math.radians(bearing_deg)
    p1 = math.radians(lat)
    l1 = math.radians(lon)
    dr = distance_m / r
    p2 = math.asin(math.sin(p1) * math.cos(dr) + math.cos(p1) * math.sin(dr) * math.cos(br))
    l2 = l1 + math.atan2(math.sin(br) * math.sin(dr) * math.cos(p1),
                         math.cos(dr) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), math.degrees(l2)


def point_in_ring(lat, lon, ring):
    """Ray casting. `ring` is a list of {lat, lon}."""
    inside = False
    n = len(ring)
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        if (a["lat"] > lat) != (b["lat"] > lat):
            x = (b["lon"] - a["lon"]) * (lat - a["lat"]) / (b["lat"] - a["lat"]) + a["lon"]
            if lon < x:
                inside = not inside
    return inside


def dist_to_segment_m(lat, lon, a, b):
    """Metres from a point to a segment, in a local flat approximation. Good
    enough at these distances and far cheaper than a proper geodesic."""
    mx = math.cos(math.radians(lat)) * 111320.0
    my = 111320.0
    px, py = lon * mx, lat * my
    ax, ay = a["lon"] * mx, a["lat"] * my
    bx, by = b["lon"] * mx, b["lat"] * my
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def near_woodland(lat, lon, woods, within_m):
    """Whether mapped woodland lies within `within_m` of the point.

    Proximity, not containment. OSM mappers draw woodland up to the road edge
    and stop, so a point on the centreline is essentially never *inside* a wood
    polygon - the first version of this tested containment and scored every
    day 0% canopy while polygons sat 20 feet away. What shades a road is trees
    beside it, which is what this measures."""
    deg = within_m / 111320.0 * 2
    for w in woods:
        lats = [v["lat"] for v in w]
        lons = [v["lon"] for v in w]
        if not (min(lats) - deg <= lat <= max(lats) + deg
                and min(lons) - deg <= lon <= max(lons) + deg):
            continue
        if point_in_ring(lat, lon, w):
            return True
        for i in range(len(w)):
            if dist_to_segment_m(lat, lon, w[i], w[(i + 1) % len(w)]) <= within_m:
                return True
    return False


# --- sun ------------------------------------------------------------------

def sun_position(dt_utc, lat, lon):
    """Solar elevation and azimuth in degrees (NOAA). Azimuth is measured
    clockwise from true north."""
    jd = (dt_utc - datetime(2000, 1, 1, 12, tzinfo=timezone.utc)).total_seconds() / 86400.0
    g = math.radians((357.529 + 0.98560028 * jd) % 360)
    q = (280.459 + 0.98564736 * jd) % 360
    lam = math.radians((q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) % 360)
    e = math.radians(23.439 - 0.00000036 * jd)

    dec = math.asin(math.sin(e) * math.sin(lam))
    ra = math.atan2(math.cos(e) * math.sin(lam), math.cos(lam))

    gmst = (18.697374558 + 24.06570982441908 * jd) % 24
    lst = (gmst + lon / 15.0) % 24
    ha = math.radians(((lst - math.degrees(ra) / 15.0) * 15 + 180) % 360 - 180)

    p = math.radians(lat)
    elev = math.asin(math.sin(p) * math.sin(dec) + math.cos(p) * math.cos(dec) * math.cos(ha))
    az = math.atan2(-math.sin(ha),
                    math.tan(dec) * math.cos(p) - math.sin(p) * math.cos(ha))
    return math.degrees(elev), math.degrees(az) % 360


# --- data sources ---------------------------------------------------------

def get_json(url, attempts=7, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    delay = 20
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 504, 502) or attempt == attempts:
                raise
            print(f"      {e.code}, waiting {delay}s", flush=True)
            time.sleep(delay)
            delay = min(delay * 2, 300)
    raise RuntimeError("unreachable")


def _load_elevation_cache():
    if ELEVATION_CACHE.exists():
        try:
            return json.loads(ELEVATION_CACHE.read_text())
        except ValueError:
            return {}
    return {}


def elevations(points):
    """Ground elevation in metres for a list of (lat, lon), batched and cached.

    The cache matters more than it looks. Terrain does not move, sample points
    are deterministic for a given route, and re-running at a different hour
    only changes the horizon probes - so without this, every re-run refetched
    hundreds of identical points and walked straight into a 429."""
    cache = _load_elevation_cache()
    key = lambda p: f"{p[0]:.5f},{p[1]:.5f}"  # noqa: E731

    missing = [p for p in points if key(p) not in cache]
    unique = list({key(p): p for p in missing}.values())
    if unique:
        print(f"      {len(points) - len(missing)} cached, fetching {len(unique)}",
              flush=True)
    for i in range(0, len(unique), ELEVATION_BATCH):
        chunk = unique[i:i + ELEVATION_BATCH]
        q = urllib.parse.urlencode({
            "latitude": ",".join(f"{p[0]:.5f}" for p in chunk),
            "longitude": ",".join(f"{p[1]:.5f}" for p in chunk),
        })
        got = get_json(f"{ELEVATION_API}?{q}")["elevation"]
        for p, e in zip(chunk, got):
            cache[key(p)] = e
        # Written after every batch so a rate limit later costs nothing
        # already paid for.
        ELEVATION_CACHE.write_text(json.dumps(cache))
        time.sleep(1.0)
    return [cache[key(p)] for p in points]


def woodland(route):
    lats = [p[0] for p in route]
    lons = [p[1] for p in route]
    bbox = "%s,%s,%s,%s" % (min(lats) - 0.02, min(lons) - 0.02,
                            max(lats) + 0.02, max(lons) + 0.02)
    els = get_json(OVERPASS,
                   data=urllib.parse.urlencode({"data": WOOD_QUERY % {"bbox": bbox}}).encode())
    return [e["geometry"] for e in els["elements"] if e.get("geometry")]


# --- analysis -------------------------------------------------------------

def sample_route(route, interval_mi):
    """Evenly spaced points along the route, with their mile markers."""
    cum = [0.0]
    for a, b in zip(route, route[1:]):
        cum.append(cum[-1] + haversine_mi(a, b))
    out = []
    target = 0.0
    for i, d in enumerate(cum):
        if d >= target:
            out.append({"mile": round(d, 2), "lat": route[i][0], "lon": route[i][1]})
            target = d + interval_mi
    return out


def routes_for(day):
    """(label, route) for the day's primary line and every alternate, so the
    options can be compared on shade rather than by eye."""
    d = json.loads((DATA_DIR / f"day-{day}.json").read_text())
    out = [("primary", d["route"])]
    for o in d.get("options", []):
        if o["gpx"] != d.get("gpx"):
            out.append((o["label"], o["route"]))
    return out


def analyse_day(day, hour_local, route, label="primary"):
    ride_date = build_data.TRIP_START + timedelta(days=day - 1)
    # Built as an offset rather than by setting the hour directly: local hours
    # from 17:00 push the UTC hour past 23 and roll into the next day.
    dt_utc = (datetime(ride_date.year, ride_date.month, ride_date.day,
                       0, 0, tzinfo=timezone.utc)
              + timedelta(hours=hour_local - PACIFIC_UTC_OFFSET_H))

    samples = sample_route(route, SAMPLE_INTERVAL_MI)
    mid = samples[len(samples) // 2]
    sun_elev, sun_az = sun_position(dt_utc, mid["lat"], mid["lon"])
    print(f"  day {day} [{label}]: {len(samples)} samples, sun {sun_elev:.1f} deg "
          f"elevation, {sun_az:.0f} deg azimuth at {hour_local}:00 local")

    if sun_elev <= 0:
        return None

    # One flat list of every point needing an elevation: each sample, then its
    # horizon probes along the sun bearing.
    query = [(s["lat"], s["lon"]) for s in samples]
    for s in samples:
        for d in HORIZON_DISTANCES_M:
            query.append(offset(s["lat"], s["lon"], sun_az, d))
    print(f"      fetching {len(query)} elevations", flush=True)
    elev = elevations(query)

    n = len(samples)
    woods = woodland(route)
    print(f"      {len(woods)} woodland polygons", flush=True)

    out = []
    for i, s in enumerate(samples):
        here = elev[i]
        blocked = False
        max_angle = 0.0
        for k, dist in enumerate(HORIZON_DISTANCES_M):
            there = elev[n + i * len(HORIZON_DISTANCES_M) + k]
            angle = math.degrees(math.atan2(there - here, dist))
            max_angle = max(max_angle, angle)
        blocked = max_angle > sun_elev
        treed = near_woodland(s["lat"], s["lon"], woods, CANOPY_WITHIN_M)
        out.append({
            "mile": s["mile"],
            "terrain_shade": blocked,
            "horizon_deg": round(max_angle, 1),
            "canopy": treed,
        })

    shaded = sum(1 for o in out if o["terrain_shade"] or o["canopy"])
    return {
        "label": label,
        "hour_local": hour_local,
        "date": ride_date.isoformat(),
        "sun_elevation_deg": round(sun_elev, 1),
        "sun_azimuth_deg": round(sun_az),
        "samples": out,
        "pct_terrain": round(100 * sum(1 for o in out if o["terrain_shade"]) / len(out)),
        "pct_canopy": round(100 * sum(1 for o in out if o["canopy"]) / len(out)),
        "pct_any_shade": round(100 * shaded / len(out)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("days", nargs="*", type=int)
    ap.add_argument("--hour", type=int, default=DEFAULT_HOUR_LOCAL)
    args = ap.parse_args()
    days = args.days or list(range(1, 9))

    path = DATA_DIR / "shade.json"
    existing = json.loads(path.read_text()) if path.exists() else {}

    print(f"shade analysis at {args.hour}:00 local:")
    for day in days:
        for label, route in routes_for(day):
            result = analyse_day(day, args.hour, route, label)
            if result is None:
                print(f"  day {day} [{label}]: sun below horizon, skipped")
                continue
            existing.setdefault(str(day), {})[label] = result
            print(f"      terrain {result['pct_terrain']}%  canopy "
                  f"{result['pct_canopy']}%  either {result['pct_any_shade']}%")
            path.write_text(json.dumps(existing, indent=1, sort_keys=True))
            time.sleep(2)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
