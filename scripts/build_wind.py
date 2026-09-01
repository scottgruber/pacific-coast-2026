#!/usr/bin/env python3
"""Work out which way the wind usually blows on each day, and whether it helps.

The question a rider actually has is not "which way does the wind come from"
but "does it push me or fight me", and that depends as much on the day's own
heading as on the weather. A northwesterly is a gift on Day 8 down the coast
and a nuisance on the stretch of Day 6 that turns inland. So this computes
both halves and reports the relationship:

  BEARING    The day's heading, as a distance-weighted circular mean of its
             segment bearings - not start-to-finish, which would call a day
             that loops out and back "no direction at all". Weighting by
             segment length keeps a few tight switchbacks from outvoting
             thirty miles of straight valley road.

  PREVAILING The vector mean of ten Septembers of hourly wind at the day's
             midpoint, restricted to riding hours. Vector rather than
             arithmetic: averaging 350 deg and 10 deg arithmetically gives
             180, the exact opposite of the truth. Vector averaging also
             makes the magnitude meaningful - a steady northwesterly keeps
             most of its speed, while a day of swirling wind averages down
             toward zero, which is itself worth knowing.

Both come from Open-Meteo's historical archive, which is free, needs no key,
and is already the elevation source for build_shade.py.

Writes data/wind.json, keyed by day. Kept out of data/day-N.json for the same
reason as shade: build_data.py rewrites those wholesale, and this is slow.

    python3 scripts/build_wind.py           # all days
    python3 scripts/build_wind.py 3 4       # just those

CAVEATS. This is climatology, not a forecast - it says what September usually
does, which is the right input when packing but not when deciding to start an
hour early. The day page still shows the live National Weather Service reading
alongside it. One sample point per day stands in for sixty miles of route, so
a day that crosses a range has one number for two very different wind regimes.
"""
import argparse
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_data  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"

# Ten Septembers is enough to average out a single odd year without reaching
# so far back that the record stops describing the present climate.
YEARS = list(range(2015, 2025))
WINDOW = ("09-01", "09-15")
# Riding hours. Wind on this coast is strongly diurnal - calm at dawn, hardest
# mid-afternoon - so including the night would halve the average and describe
# a day nobody rides.
RIDING_HOURS = range(9, 18)
# Within this many degrees of the direction of travel counts as a head or tail
# wind; everything else is a crosswind. 45 splits the compass into four equal
# quarters, which is the honest reading of a single averaged direction.
SECTOR_DEG = 45.0


def bearing(a, b):
    """Initial compass bearing from a to b, degrees clockwise from north."""
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlon = math.radians(b[1] - a[1])
    x = math.sin(dlon) * math.cos(lat2)
    y = (math.cos(lat1) * math.sin(lat2)
         - math.sin(lat1) * math.cos(lat2) * math.cos(dlon))
    return math.degrees(math.atan2(x, y)) % 360


def route_bearing(route):
    """Distance-weighted circular mean of the route's segment bearings."""
    x = y = 0.0
    for a, b in zip(route, route[1:]):
        d = build_data.haversine_m(a, b)
        if d < 1:
            continue
        th = math.radians(bearing(a, b))
        x += d * math.cos(th)
        y += d * math.sin(th)
    if x == 0 and y == 0:
        return None
    return math.degrees(math.atan2(y, x)) % 360


def compass(deg):
    names = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
             "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return names[int((deg % 360) / 22.5 + 0.5) % 16]


def midpoint(route):
    return route[len(route) // 2]


def fetch_year(points, year):
    """One call for every day's midpoint, for one September."""
    q = urllib.parse.urlencode({
        "latitude": ",".join(f"{p[0]:.4f}" for p in points),
        "longitude": ",".join(f"{p[1]:.4f}" for p in points),
        "start_date": f"{year}-{WINDOW[0]}",
        "end_date": f"{year}-{WINDOW[1]}",
        "hourly": "wind_speed_10m,wind_direction_10m",
        "wind_speed_unit": "mph",
        "timezone": "America/Los_Angeles",
    })
    for attempt in range(5):
        try:
            with urllib.request.urlopen(f"{ARCHIVE}?{q}", timeout=120) as r:
                d = json.loads(r.read().decode())
            return d if isinstance(d, list) else [d]
        except Exception as exc:
            print(f"    {year} retry {attempt + 1}/5: {exc}")
            time.sleep(10 * (attempt + 1))
    raise SystemExit(f"archive API unavailable for {year}")


def relation(wind_from_deg, travel_deg):
    """How the wind sits relative to the direction of travel.

    Wind direction is meteorological - the direction it blows FROM. A wind
    from the same bearing you are riding toward is in your face."""
    diff = (wind_from_deg - travel_deg + 180) % 360 - 180
    a = abs(diff)
    if a <= SECTOR_DEG:
        kind = "headwind"
    elif a >= 180 - SECTOR_DEG:
        kind = "tailwind"
    else:
        kind = "crosswind"
    # Signed component along the direction of travel: positive helps.
    return kind, -math.cos(math.radians(diff)), diff


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("days", nargs="*", type=int)
    args = ap.parse_args()
    days = args.days or sorted(build_data.GPX_FILES)

    routes = {}
    for n in days:
        d = json.loads((DATA_DIR / f"day-{n}.json").read_text())
        routes[n] = d["route"]
    pts = [midpoint(routes[n]) for n in days]

    # Sum wind as vectors across every year, then divide once at the end.
    # [vector x, vector y, count, scalar speed sum]. The scalar sum matters:
    # the vector magnitude collapses toward zero when the direction wanders, so
    # on its own it would report a blustery, variable day as a calm one.
    acc = {n: [0.0, 0.0, 0, 0.0] for n in days}
    for year in YEARS:
        print(f"  {year} ...", flush=True)
        for n, block in zip(days, fetch_year(pts, year)):
            h = block["hourly"]
            for t, spd, drc in zip(h["time"], h["wind_speed_10m"],
                                   h["wind_direction_10m"]):
                if spd is None or drc is None:
                    continue
                if int(t[11:13]) not in RIDING_HOURS:
                    continue
                th = math.radians(drc)
                acc[n][0] += spd * math.sin(th)
                acc[n][1] += spd * math.cos(th)
                acc[n][2] += 1
                acc[n][3] += spd
        time.sleep(1)

    out = {}
    for n in days:
        ex, ey, cnt, ssum = acc[n]
        if not cnt:
            continue
        ex, ey = ex / cnt, ey / cnt
        steadiness = math.hypot(ex, ey)
        speed = ssum / cnt
        frm = math.degrees(math.atan2(ex, ey)) % 360
        travel = route_bearing(routes[n])
        kind, comp, diff = relation(frm, travel)
        out[str(n)] = {
            "from_deg": round(frm),
            "from_compass": compass(frm),
            # What it actually blows, and how much of that is in one
            # direction. A 12 mph mean with 9 mph of it steady is a wind you
            # can plan around; the same mean at 2 mph steady is just gusty.
            "speed_mph": round(speed, 1),
            "steady_mph": round(steadiness, 1),
            "consistency": round(steadiness / speed, 2) if speed else 0,
            "travel_deg": round(travel),
            "travel_compass": compass(travel),
            "relation": kind,
            "component": round(comp, 2),
            "hours": cnt,
            "years": [YEARS[0], YEARS[-1]],
            "hours_local": [min(RIDING_HOURS), max(RIDING_HOURS)],
        }
        print(f"day {n}: riding {compass(travel):<3} ({travel:3.0f}deg), "
              f"wind from {compass(frm):<3} at {speed:4.1f} mph mean "
              f"({steadiness:4.1f} steady, {steadiness / speed:.0%} consistent) "
              f"-> {kind} ({comp:+.2f})")

    path = DATA_DIR / "wind.json"
    merged = {}
    if path.exists():
        merged = json.loads(path.read_text())
    merged.update(out)
    path.write_text(json.dumps(merged, indent=1, sort_keys=True))
    print(f"\nwrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
