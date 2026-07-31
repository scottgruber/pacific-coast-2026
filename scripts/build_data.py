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
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.setrecursionlimit(10000)

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
STOPS = [
    "Los Altos", "Santa Cruz", "Monterey", "Big Sur", "San Simeon",
    "Oceano", "Solvang", "Ventura", "Santa Monica",
]

TOWNS = {n: {"start": STOPS[n - 1], "end": STOPS[n]} for n in range(1, len(STOPS))}

GPX_FILES = {
    1: "Day-1-Los-Altos-to-Santa-Cruz.gpx",
    2: "Day-2-Santa-Cruz-to-Monterey.gpx",
    3: "Day-3-Monterey-to-Big-Sur.gpx",
    4: "Day-4-Big-Sur-to-San-Simeon.gpx",
    5: "Day-5-San-Simeon-to-Oceano.gpx",
    6: "Day-6-Oceano-to-Solvang.gpx",
    7: "Day-7-Solvang-to-Ventura.gpx",
    8: "Day-8-Ventura-to-Santa-Monica.gpx",
}
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
        waypoints.append({
            "lat": float(wpt.get("lat")),
            "lon": float(wpt.get("lon")),
            "name": name_el.text.strip() if name_el is not None and name_el.text else "",
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


def build_day(n):
    pts, waypoints = parse_gpx(GPX_DIR / GPX_FILES[n])
    route = simplify_route(pts, RDP_TOLERANCE_M)
    elevation = elevation_profile(pts, ELEV_RESAMPLE_INTERVAL_M, ELEV_SMOOTHING_WINDOW)
    # First/last wpt are the day's start/end (lodging); anything in between
    # is a real waypoint worth marking on the map (e.g. a landmark rest stop).
    mid_waypoints = waypoints[1:-1] if len(waypoints) > 2 else []
    date = TRIP_START + datetime.timedelta(days=n - 1)
    data = {
        "day": n,
        "pending": False,
        "date": date.isoformat(),
        "date_label": date.strftime("%A, %B %-d"),
        "towns": TOWNS[n],
        "stats": {"distance_mi": elevation["distance_mi"]},
        "elevation": elevation,
        "route": route,
        "waypoints": mid_waypoints,
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
        "stats": None,
        "elevation": None,
        "route": None,
        "waypoints": [],
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
