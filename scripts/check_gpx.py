#!/usr/bin/env python3
"""Audit the published GPX tracks for the things that break a head unit or
mislead a rider.

These files are the part of the project somebody's safety actually rests on.
Everything else on the site can be wrong and cost a reader nothing; a track
that jumps, loses its elevation, or stops a mile from the bed costs them at
the end of a sixty-mile day.

Local and fast — no network. Run it after any change to gpx/, and before a
deploy. Road classes are a separate question; see check_roads.py.

    python3 scripts/check_gpx.py          # every day
    python3 scripts/check_gpx.py 3 4      # just those

Exits non-zero if anything is an error, so it can gate a deploy.

WHAT A WARNING IS NOT. A large gap between consecutive points is reported but
is not an error: day 5 runs 2,228 ft between points on Los Osos Valley Road,
and the road is dead straight there, so the drawn line follows it exactly.
Sparse sampling on a straight road is fine. Sparse sampling on a bend is not,
and this script cannot tell the two apart without the road geometry — so it
says which points to look at rather than pretending to judge.
"""
import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
GPX_DIR = ROOT / "gpx"
DATA_DIR = ROOT / "data"
NS = "{http://www.topografix.com/GPX/1/1}"

# A head unit draws the line it is given. Past this, the line visibly leaves
# the road on anything but a straight — worth a human looking at the spot.
GAP_WARN_FT = 600.0
# Past this it is not sparse sampling, it is a hole.
GAP_ERROR_FT = 4000.0
# Start and Finish are written from the track's own ends by prepare_gpx.py, so
# any disagreement means the file was edited without re-running it.
ENDPOINT_TOLERANCE_FT = 50.0
# Each day starts where the last one finished. A block or so of slack absorbs
# one-way streets and which side of a forecourt a track was drawn to.
CHAIN_TOLERANCE_FT = 300.0
# Under this, "the route ends at the hotel" is true enough to say without a
# number. It matches LODGING_GAP_NOTABLE_MI in build_data.py.
HOTEL_TOLERANCE_FT = 528.0
# Garmin caps course points, and turn cues compete with POIs for the budget.
WAYPOINT_WARN = 200


def haversine_ft(a, b):
    r = 20902231.0
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(h))


class Report:
    def __init__(self, label):
        self.label = label
        self.errors = []
        self.warnings = []
        self.facts = {}

    def error(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)


def audit_file(path, report):
    """Structure, elevation and continuity of one track. Returns its points."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        report.error(f"XML will not parse: {e}")
        return None
    if not root.tag.startswith(NS[:-1]):
        report.error(f"root element is not GPX 1.1: {root.tag}")

    segments = root.findall(f"{NS}trk/{NS}trkseg")
    if len(segments) != 1:
        report.warn(f"{len(segments)} track segments; a head unit may read "
                    f"breaks as separate courses")

    pts, eles, missing_ele = [], [], 0
    for node in root.iter(NS + "trkpt"):
        try:
            lat, lon = float(node.get("lat")), float(node.get("lon"))
        except (TypeError, ValueError):
            report.error("track point with missing or unparseable lat/lon")
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            report.error(f"coordinate out of range: {lat},{lon}")
        pts.append((lat, lon))
        ele = node.find(NS + "ele")
        try:
            eles.append(float(ele.text))
        except (AttributeError, TypeError, ValueError):
            missing_ele += 1

    if not pts:
        report.error("no track points")
        return None
    report.facts["points"] = len(pts)

    if missing_ele:
        report.error(f"{missing_ele} points without usable <ele>; climb totals "
                     f"and the elevation profile will be wrong")
    if eles:
        lo, hi = min(eles), max(eles)
        report.facts["ele_m"] = (round(lo), round(hi))
        if lo < -100 or hi > 4000:
            report.error(f"implausible elevation range {lo:.0f}..{hi:.0f} m")

    gaps = [haversine_ft(a, b) for a, b in zip(pts, pts[1:])]
    report.facts["miles"] = round(sum(gaps) / 5280.0, 1)
    report.facts["max_gap_ft"] = round(max(gaps))
    duplicates = sum(1 for g in gaps if g == 0)
    if duplicates:
        report.warn(f"{duplicates} zero-length point pairs")
    for i, g in enumerate(gaps):
        if g >= GAP_ERROR_FT:
            report.error(f"{g:.0f} ft between points {i} and {i + 1} "
                         f"({pts[i]} to {pts[i + 1]}) — that is a hole")
    big = [(g, i) for i, g in enumerate(gaps) if GAP_WARN_FT <= g < GAP_ERROR_FT]
    if big:
        worst, at = max(big)
        report.warn(f"{len(big)} gaps over {GAP_WARN_FT:.0f} ft, worst "
                    f"{worst:.0f} ft at point {at} {pts[at]} — fine on a "
                    f"straight, check any that fall on a bend")

    wpts = root.findall(NS + "wpt")
    report.facts["waypoints"] = len(wpts)
    if len(wpts) > WAYPOINT_WARN:
        report.warn(f"{len(wpts)} waypoints, near Garmin's course-point cap")
    for w in wpts:
        try:
            float(w.get("lat")), float(w.get("lon"))
        except (TypeError, ValueError):
            report.error("waypoint with unparseable coordinates")
        if not (w.findtext(NS + "name") or "").strip():
            report.error("waypoint with no name")
    for kind, index, where in (("Start", 0, "first"), ("Finish", -1, "last")):
        marks = [w for w in wpts if (w.findtext(NS + "type") or "") == kind]
        if len(marks) != 1:
            report.error(f"{len(marks)} {kind} waypoints, expected exactly 1")
            continue
        off = haversine_ft(
            (float(marks[0].get("lat")), float(marks[0].get("lon"))), pts[index])
        if off > ENDPOINT_TOLERANCE_FT:
            report.error(f"{kind} waypoint sits {off:.0f} ft from the track's "
                         f"{where} point; re-run prepare_gpx.py")
    return pts


def audit_trip(days, ends):
    """The checks no single file can make: that the days join up, and that each
    one finishes where the night is booked."""
    report = Report("trip")
    lodging_path = DATA_DIR / "lodging.json"
    lodging = json.loads(lodging_path.read_text()) if lodging_path.exists() else {}
    import build_data

    for n in sorted(ends):
        if n + 1 not in ends:
            continue
        off = haversine_ft(ends[n][-1], ends[n + 1][0])
        if off > CHAIN_TOLERANCE_FT:
            report.error(f"day {n} finishes {off:.0f} ft from where day "
                         f"{n + 1} starts")
    for n in sorted(ends):
        hotel = lodging.get(build_data.STOPS[n])
        if not hotel:
            report.warn(f"day {n} has no hotel recorded for "
                        f"{build_data.STOPS[n]}")
            continue
        off = haversine_ft((hotel["lat"], hotel["lon"]), ends[n][-1])
        if off > HOTEL_TOLERANCE_FT:
            report.warn(f"day {n} finishes {off:.0f} ft from {hotel['name']}; "
                        f"the page will print the distance")
    if len(days) < 8:
        report.warn("only some days were checked, so chaining is partial")
    return report


def main():
    import build_data
    days = [int(a) for a in sys.argv[1:]] or sorted(build_data.GPX_FILES)
    reports, ends = [], {}

    for n in days:
        path = GPX_DIR / build_data.GPX_FILES[n]
        report = Report(f"day {n}")
        if not path.exists():
            report.error(f"missing file: {path.name}")
        else:
            pts = audit_file(path, report)
            if pts:
                ends[n] = pts
        reports.append(report)

    print(f"{'':7s}{'points':>8}{'miles':>8}{'max gap':>9}{'wpts':>6}  elevation m")
    for r in reports:
        f = r.facts
        print(f"{r.label:7s}{f.get('points', 0):8d}{f.get('miles', 0):8.1f}"
              f"{f.get('max_gap_ft', 0):8d}f{f.get('waypoints', 0):6d}  "
              f"{f.get('ele_m', '-')}")

    reports.append(audit_trip(days, ends))
    errors = warnings = 0
    for r in reports:
        if not (r.errors or r.warnings):
            continue
        print(f"\n{r.label}")
        for e in r.errors:
            print(f"  ERROR  {e}")
        for w in r.warnings:
            print(f"  warn   {w}")
        errors += len(r.errors)
        warnings += len(r.warnings)

    print(f"\n{errors} error(s), {warnings} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
