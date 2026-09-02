#!/usr/bin/env python3
"""Parse per-day GPX tracks (and the Section 4 reference track) into
lightweight JSON for the site templates.

Unlike a post-trip recap, this route hasn't been ridden yet — there's no
recorded moving time or average speed, so day stats are limited to what a
route file can tell you: distance, elevation gain/loss, and notable climbs.

Reads gpx/Day-N-*.gpx, computes a simplified route polyline (Ramer-Douglas-
Peucker) and a resampled/smoothed elevation profile per day, plus the same
for the Pacific-Coast-Section-4-SF-SB-Southbound.gpx reference track, and
writes data/day-N.json + data/reference.json + data/overview.json.

Re-run this whenever a GPX file changes, then re-run generate_pages.py to
refresh the HTML.
"""
import datetime
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.setrecursionlimit(10000)

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
GPX_DIR = ROOT / "gpx"
DATA_DIR = ROOT / "data"
NS = "{http://www.topografix.com/GPX/1/1}"

RDP_TOLERANCE_M = 4.0
ELEV_RESAMPLE_INTERVAL_M = 100.0
ELEV_SMOOTHING_WINDOW = 5

M_TO_MI = 0.000621371
M_TO_FT = 3.28084

# Ride order — day N starts where day N-1 ended, so each stop is a single
# name in this list instead of being duplicated per day.
#
# Days 3-5 were rerouted inland in Aug 2026 after a Big Sur fire closed the
# coast route (Monterey-Big Sur-San Simeon-Oceano). The superseded tracks are
# kept in gpx/unused/ rather than deleted, in case the coast reopens.
STOPS = [
    "Los Altos", "Santa Cruz", "Monterey", "King City", "Paso Robles",
    "Oceano", "Solvang", "Ventura", "Santa Monica",
]

TOWNS = {n: {"start": STOPS[n - 1], "end": STOPS[n]} for n in range(1, len(STOPS))}

GPX_FILES = {
    1: "Day-1-Los-Altos-to-Santa-Cruz.gpx",
    2: "Day-2-Santa-Cruz-to-Monterey.gpx",
    3: "Day-3-Monterey_to_King_City-via-Salinas-Valley-River-Road.gpx",
    4: "Day-4-King_City_to_Paso_Robles.gpx",
    5: "Day-5-Paso_Robles_to_Oceano-via-Santa-Rita-and-Los-Osos.gpx",
    6: "Day-6-Oceano-to-Solvang.gpx",
    7: "Day-7-Solvang-to-Ventura.gpx",
    8: "Day-8-Ventura-to-Santa-Monica.gpx",
}

# Days where more than one candidate track is still in play. The file named in
# GPX_FILES above is the primary (it drives the day's headline stats and
# elevation chart); everything listed here is drawn and offered alongside it so
# the options can be compared before one is committed to.
# Day 3 is settled on River Road and no longer lists options; the Carmel
# Valley track stays in gpx/ as the record of what it was weighed against.
ROUTE_OPTIONS = {}

PENDING_DAYS = []
ALL_DAYS = sorted(set(GPX_FILES) | set(PENDING_DAYS))

REFERENCE_GPX = GPX_DIR / "Pacific-Coast-Section-4-SF-SB-Southbound.gpx"
REFERENCE_NAME = "Section 4: SF–Santa Barbara (Southbound)"

TRIP_START = datetime.date(2026, 9, 5)
TRIP_END = datetime.date(2026, 9, 12)


def haversine_m(a, b):
    lat1, lon1 = a
    lat2, lon2 = b
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(x))


def parse_gpx(path):
    tree = ET.parse(path)
    root = tree.getroot()
    pts = []
    for trkpt in root.iter(NS + "trkpt"):
        lat = float(trkpt.get("lat"))
        lon = float(trkpt.get("lon"))
        ele_el = trkpt.find(NS + "ele")
        ele = float(ele_el.text) if ele_el is not None else 0.0
        pts.append((lat, lon, ele))
    waypoints = []
    for wpt in root.findall(NS + "wpt"):
        name_el = wpt.find(NS + "name")
        type_el = wpt.find(NS + "type")
        cmt_el = wpt.find(NS + "cmt")
        waypoints.append({
            "lat": float(wpt.get("lat")),
            "lon": float(wpt.get("lon")),
            "name": name_el.text.strip() if name_el is not None and name_el.text else "",
            "type": type_el.text.strip() if type_el is not None and type_el.text else "",
            "cmt": cmt_el.text.strip() if cmt_el is not None and cmt_el.text else "",
        })
    return pts, waypoints


def to_local_xy(pts):
    """Equirectangular projection to meters, local to this track's mean latitude."""
    lat0 = math.radians(sum(p[0] for p in pts) / len(pts))
    xs, ys = [], []
    for lat, lon, _ in pts:
        x = math.radians(lon) * math.cos(lat0) * 6371000.0
        y = math.radians(lat) * 6371000.0
        xs.append(x)
        ys.append(y)
    return xs, ys


def rdp(points, tolerance):
    """Ramer-Douglas-Peucker simplification. points: list of (x, y). Returns
    indices into the original list to keep."""

    def perp_dist(pt, start, end):
        if start == end:
            return math.hypot(pt[0] - start[0], pt[1] - start[1])
        x0, y0 = pt
        x1, y1 = start
        x2, y2 = end
        num = abs((y2 - y1) * x0 - (x2 - x1) * y0 + x2 * y1 - y2 * x1)
        den = math.hypot(y2 - y1, x2 - x1)
        return num / den

    def simplify(idx_range):
        start_i, end_i = idx_range[0], idx_range[-1]
        if end_i <= start_i + 1:
            return [start_i, end_i]
        start, end = points[start_i], points[end_i]
        max_dist = -1.0
        max_i = start_i
        for i in idx_range[1:-1]:
            d = perp_dist(points[i], start, end)
            if d > max_dist:
                max_dist = d
                max_i = i
        if max_dist > tolerance:
            left = simplify(list(range(start_i, max_i + 1)))
            right = simplify(list(range(max_i, end_i + 1)))
            return left[:-1] + right
        return [start_i, end_i]

    return simplify(list(range(len(points))))


def simplify_route(pts, tolerance_m):
    xs, ys = to_local_xy(pts)
    xy = list(zip(xs, ys))
    keep_idx = rdp(xy, tolerance_m)
    return [[round(pts[i][0], 5), round(pts[i][1], 5)] for i in keep_idx]


MIN_CLIMB_GRADIENT_PCT = 2.5
MIN_CLIMB_GAIN_M = 20.0
MIN_CLIMB_LENGTH_M = 500.0
CLIMB_MERGE_GAP_M = 250.0


def detect_climbs(sample_dists, smoothed):
    """Find sustained-climb segments in a resampled/smoothed elevation
    profile: contiguous stretches at or above MIN_CLIMB_GRADIENT_PCT,
    merging stretches separated by a short flat/rolling gap, then dropping
    anything too short or too shallow to matter."""
    grades = []
    for i in range(len(smoothed) - 1):
        dd = sample_dists[i + 1] - sample_dists[i]
        de = smoothed[i + 1] - smoothed[i]
        grades.append((de / dd * 100.0) if dd > 0 else 0.0)

    raw_runs = []
    i = 0
    while i < len(grades):
        if grades[i] >= MIN_CLIMB_GRADIENT_PCT:
            j = i
            while j < len(grades) and grades[j] >= MIN_CLIMB_GRADIENT_PCT:
                j += 1
            raw_runs.append([i, j])
            i = j
        else:
            i += 1

    merged_runs = []
    for run in raw_runs:
        if merged_runs and (sample_dists[run[0]] - sample_dists[merged_runs[-1][1]]) <= CLIMB_MERGE_GAP_M:
            merged_runs[-1][1] = run[1]
        else:
            merged_runs.append(run)

    climbs = []
    for start_i, end_i in merged_runs:
        length_m = sample_dists[end_i] - sample_dists[start_i]
        gain_m = smoothed[end_i] - smoothed[start_i]
        if length_m < MIN_CLIMB_LENGTH_M or gain_m < MIN_CLIMB_GAIN_M:
            continue
        climbs.append({
            "start_mi": round(sample_dists[start_i] * M_TO_MI, 2),
            "end_mi": round(sample_dists[end_i] * M_TO_MI, 2),
            "length_mi": round(length_m * M_TO_MI, 2),
            "gain_ft": round(gain_m * M_TO_FT),
            "avg_gradient_pct": round(gain_m / length_m * 100.0, 1),
        })
    return climbs


def elevation_profile(pts, interval_m, smoothing_window):
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + haversine_m(a[:2], b[:2]))
    total = cum[-1]

    n_samples = max(2, int(total // interval_m) + 1)
    sample_dists = [i * interval_m for i in range(n_samples)]
    if sample_dists[-1] < total:
        sample_dists.append(total)

    eles = [p[2] for p in pts]
    resampled = []
    j = 0
    for d in sample_dists:
        while j < len(cum) - 2 and cum[j + 1] < d:
            j += 1
        d0, d1 = cum[j], cum[j + 1] if j + 1 < len(cum) else cum[j]
        e0, e1 = eles[j], eles[j + 1] if j + 1 < len(eles) else eles[j]
        if d1 > d0:
            t = (d - d0) / (d1 - d0)
            e = e0 + t * (e1 - e0)
        else:
            e = e0
        resampled.append(e)

    smoothed = []
    half = smoothing_window // 2
    for i in range(len(resampled)):
        lo = max(0, i - half)
        hi = min(len(resampled), i + half + 1)
        window = resampled[lo:hi]
        smoothed.append(sum(window) / len(window))

    gain = loss = 0.0
    for a, b in zip(smoothed, smoothed[1:]):
        d = b - a
        if d > 0:
            gain += d
        else:
            loss += -d

    profile = [
        {"d_mi": round(d * M_TO_MI, 2), "ele_m": round(e, 1)}
        for d, e in zip(sample_dists, smoothed)
    ]
    climbs = detect_climbs(sample_dists, smoothed)
    return {
        "profile": profile,
        "distance_mi": round(total * M_TO_MI, 1),
        "gain_m": round(gain, 1),
        "loss_m": round(loss, 1),
        "gain_ft": round(gain * M_TO_FT),
        "loss_ft": round(loss * M_TO_FT),
        "min_m": round(min(eles), 1),
        "max_m": round(max(eles), 1),
        "min_ft": round(min(eles) * M_TO_FT),
        "max_ft": round(max(eles) * M_TO_FT),
        "climbs": climbs,
    }


# Written by add_services.py from OpenStreetMap. Kept out of the map-marker
# waypoints and surfaced as their own list on the day page instead.
#
# Imported rather than restated: this list was hard-coded once and immediately
# drifted - adding winery and picnic categories to add_services.py left them
# missing here, so they were silently misfiled as landmarks and never reached
# a day page. One definition, one place.
def _service_types():
    try:
        import add_services
        return set(add_services.ALL_TYPES)
    except Exception:
        # add_services pulls no unusual imports, but the build must not depend
        # on it: fall back to the known set rather than failing outright.
        return {"Water", "Toilets", "Food", "Store", "Scenic", "Historic",
                "Winery", "Picnic"}


# The provenance sentence add_services.py stamps into a hand-picked stop's
# <cmt>. Imported for the same reason as the type list above: two copies of a
# literal string in two scripts is one edit away from disagreeing.
def _hand_picked_mark():
    try:
        import add_services
        return add_services.HAND_PICKED_MARK
    except Exception:
        return "Added by hand."


# The quarter-mile screen add_services.py applies to scraped stops. Imported
# for the same reason as the two above.
def _max_offset_mi():
    try:
        import add_services
        return add_services.MAX_OFFSET_MI
    except Exception:
        return 0.25


MAX_OFFSET_MI = _max_offset_mi()
HAND_PICKED_MARK = _hand_picked_mark()
SERVICE_WAYPOINT_TYPES = _service_types()
# The day's own start and finish, added by prepare_gpx.py for the head unit.
# They are already the ends of the drawn line, so they are not marked again.
ENDPOINT_WAYPOINT_TYPES = {"Start", "Finish"}
TURN_CUE_WAYPOINT_TYPES = {"Dot"}
# A place on the route that matters for its own sake rather than as somewhere to
# stop — the Santa Monica Pier is the finish line even though day 8 carries on
# another third of a mile to the hotel. Always shown: unlike the landmark
# heuristic below, a milestone is there because somebody put it there.
MILESTONE_WAYPOINT_TYPES = {"Milestone"}


def longest_water_gap(pts, services):
    """Longest stretch, in miles, with no mapped drinking water — counting the
    day's start and finish as known stops.

    This is the number that matters for planning an inland day in September,
    and it is not visible from a list of waypoints. It reflects OSM coverage,
    not the ground: a long gap means nobody has mapped anything there."""
    total = sum(haversine_m(a[:2], b[:2]) for a, b in zip(pts, pts[1:])) * M_TO_MI
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + haversine_m(a[:2], b[:2]) * M_TO_MI)
    stops = [0.0]
    for w in services:
        if w["type"] != "Water":
            continue
        d = [haversine_m((w["lat"], w["lon"]), p[:2]) for p in pts]
        stops.append(cum[d.index(min(d))])
    stops.append(total)
    stops.sort()
    return round(max(b - a for a, b in zip(stops, stops[1:])), 1)


def build_route_option(opt):
    """Summarize one candidate track for a day that has several.

    Carries its own polyline, headline numbers and elevation profile, so the
    day page can redraw both the map line and the elevation chart when a
    visitor previews a different option. The day's *official* stats still come
    from the primary track — previewing an option never changes them."""
    pts, _ = parse_gpx(GPX_DIR / opt["file"])
    elevation = elevation_profile(pts, ELEV_RESAMPLE_INTERVAL_M, ELEV_SMOOTHING_WINDOW)
    return {
        "label": opt["label"],
        "note": opt.get("note", ""),
        "gpx": opt["file"],
        "route": simplify_route(pts, RDP_TOLERANCE_M),
        "distance_mi": elevation["distance_mi"],
        "gain_ft": elevation["gain_ft"],
        "loss_ft": elevation["loss_ft"],
        "max_ft": elevation["max_ft"],
        "profile": elevation["profile"],
    }


# Scraped stops are capped at a quarter mile off route, so their offset is
# never worth mentioning. Hand-picked ones skip that filter, which means one
# can sit miles away — and on the page it would look exactly like a stop you
# ride past. Surface the offset for those, and only those.
def far_off_route(cmt):
    """Miles off route, if that is far enough to be worth warning about."""
    m = re.search(r", ([\d.]+) mi off route", cmt)
    if not m:
        return None
    off = float(m.group(1))
    return round(off, 1) if off > MAX_OFFSET_MI else None


# data/lodging.json is keyed by town rather than by day, because a hotel is the
# end of one day and the start of the next - one entry, so the two can never
# drift apart. Los Altos is absent: day 1 starts from home.
def load_lodging():
    path = DATA_DIR / "lodging.json"
    if not path.exists():
        return {}
    return {k: v for k, v in json.loads(path.read_text()).items()
            if not k.startswith("_")}


LODGING = load_lodging()

# Most days were drawn to finish at the hotel door. Where one wasn't, the gap
# is worth stating rather than leaving a reader to assume the route ends at the
# bed - the last half mile of a 60-mile day is the one you want to know about.
# Below this, "at the finish" is true enough and the number is just noise. In
# miles rather than feet, because every other distance on the site is, and a
# gap this small only survives rounding to one decimal above ~0.05 mi.
LODGING_GAP_NOTABLE_MI = 0.1


def lodging_for(town, endpoint):
    """The night's hotel, with how far it sits from where the route stops."""
    hotel = LODGING.get(town)
    if not hotel:
        return None
    gap_m = haversine_m((hotel["lat"], hotel["lon"]), endpoint[:2])
    gap_mi = gap_m * M_TO_MI
    notable = gap_mi >= LODGING_GAP_NOTABLE_MI
    return dict(hotel, town=town,
                gap_mi=round(gap_mi, 1) if notable else None,
                gap_km=round(gap_m / 1000.0, 1) if notable else None)


def build_day(n):
    pts, waypoints = parse_gpx(GPX_DIR / GPX_FILES[n])
    route = simplify_route(pts, RDP_TOLERANCE_M)
    elevation = elevation_profile(pts, ELEV_RESAMPLE_INTERVAL_M, ELEV_SMOOTHING_WINDOW)
    # Waypoints arrive from three sources and mean different things, so they
    # are split by <type> rather than by position. The old first/last-is-lodging
    # heuristic silently turned RideWithGPS turn cues into map markers — days 3
    # and 4 were rendering dozens of dots labelled "Right" and "Left".
    #
    #   Dot             - RideWithGPS turn cues. Never shown; the map is not a
    #                     cue sheet, and the head unit already has them.
    #   Water/Toilets   - added by scripts/add_services.py from OpenStreetMap.
    #   GENERIC (other) - lodging and landmarks. First/last are the day's start
    #                     and end, so only the ones in between are marked.
    services = []
    for w in waypoints:
        if w["type"] not in SERVICE_WAYPOINT_TYPES:
            continue
        w = dict(w)
        # add_services.py writes "Name (mi 12.3)"; split it back apart so the
        # page can sort by distance and show the label on its own.
        m = re.match(r"^(.*?)\s*\(mi ([\d.]+)\)$", w["name"])
        w["label"], w["mile"] = (m.group(1), float(m.group(2))) if m else (w["name"], 0.0)
        # add_services.py stamps provenance and offset into the comment, for
        # the head unit. The page needs both, so read them back out rather than
        # re-reading manual-pois.json: the GPX is what was actually built.
        cmt = w.pop("cmt", "")
        w["hand_picked"] = HAND_PICKED_MARK in cmt
        w["detour_mi"] = far_off_route(cmt)
        services.append(w)
    services.sort(key=lambda x: x["mile"])
    milestones = [w for w in waypoints
                  if w["type"] in MILESTONE_WAYPOINT_TYPES]
    landmarks = [w for w in waypoints
                 if w["type"] not in SERVICE_WAYPOINT_TYPES
                 and w["type"] not in ENDPOINT_WAYPOINT_TYPES
                 and w["type"] not in TURN_CUE_WAYPOINT_TYPES
                 and w["type"] not in MILESTONE_WAYPOINT_TYPES]
    mid_waypoints = milestones + (landmarks[1:-1] if len(landmarks) > 2 else [])
    options = [build_route_option(o) for o in ROUTE_OPTIONS.get(n, [])]
    date = TRIP_START + datetime.timedelta(days=n - 1)
    data = {
        "day": n,
        "pending": False,
        "date": date.isoformat(),
        "date_label": date.strftime("%A, %B %-d"),
        "towns": TOWNS[n],
        "gpx": GPX_FILES[n],
        "stats": {"distance_mi": elevation["distance_mi"]},
        "elevation": elevation,
        "route": route,
        "options": options,
        "waypoints": mid_waypoints,
        "lodging": lodging_for(TOWNS[n]["end"], pts[-1]),
        "services": services,
        "water_gap_mi": longest_water_gap(pts, services),
        "route_point_count_raw": len(pts),
    }
    (DATA_DIR / f"day-{n}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"day {n}: {len(pts)} raw pts -> {len(route)} route pts, "
          f"{elevation['distance_mi']}mi, gain {elevation['gain_ft']}ft")
    return data


def build_pending_day(n):
    date = TRIP_START + datetime.timedelta(days=n - 1)
    data = {
        "day": n,
        "pending": True,
        "date": date.isoformat(),
        "date_label": date.strftime("%A, %B %-d"),
        "towns": TOWNS[n],
        "gpx": None,
        "stats": None,
        "elevation": None,
        "route": None,
        "options": [],
        "waypoints": [],
        "lodging": LODGING.get(TOWNS[n]["end"]),
    }
    (DATA_DIR / f"day-{n}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"day {n}: pending (no GPX yet)")
    return data


def build_reference():
    pts, waypoints = parse_gpx(REFERENCE_GPX)
    # The reference track is long (382mi) and already coarse — a wider
    # tolerance than the day tracks keeps the file size sane without
    # losing the route's shape at map scale.
    route = simplify_route(pts, 20.0)
    elevation = elevation_profile(pts, ELEV_RESAMPLE_INTERVAL_M, ELEV_SMOOTHING_WINDOW)
    data = {
        "name": REFERENCE_NAME,
        "route": route,
        "stats": {"distance_mi": elevation["distance_mi"]},
        "elevation": elevation,
    }
    (DATA_DIR / "reference.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"reference: {len(pts)} raw pts -> {len(route)} route pts, "
          f"{elevation['distance_mi']}mi, gain {elevation['gain_ft']}ft")
    return data


def build_overview(day_data):
    known = [d for d in day_data if not d["pending"]]
    days = [
        {
            "day": d["day"],
            "pending": d["pending"],
            "date": d["date"],
            "date_label": d["date_label"],
            "towns": d["towns"],
            "route": d.get("route"),
            "distance_mi": d["stats"]["distance_mi"] if d["stats"] else None,
            "gain_ft": d["elevation"]["gain_ft"] if d["elevation"] else None,
        }
        for d in day_data
    ]
    data = {
        "trip_start": TRIP_START.isoformat(),
        "trip_end": TRIP_END.isoformat(),
        "total_days": len(day_data),
        "known_days": len(known),
        "distance_mi": round(sum(d["stats"]["distance_mi"] for d in known), 1),
        "gain_ft": round(sum(d["elevation"]["gain_ft"] for d in known)),
        "days": days,
    }
    (DATA_DIR / "overview.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print("overview.json written")


def main():
    DATA_DIR.mkdir(exist_ok=True)
    day_data = []
    for n in ALL_DAYS:
        if n in PENDING_DAYS:
            day_data.append(build_pending_day(n))
        else:
            day_data.append(build_day(n))
    build_overview(day_data)
    build_reference()


if __name__ == "__main__":
    main()
