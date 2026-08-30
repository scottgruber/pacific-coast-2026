#!/usr/bin/env python3
"""Tidy the day GPX files for import into Ride with GPS and sync to a Garmin.

Run after add_services.py. Idempotent — safe to re-run at any time.

Four things, each fixing something that actually bites on the head unit:

1. STRIP TURN CUES. The RideWithGPS exports carry <wpt type="Dot"> entries
   named "Right", "Left", "Sharp Right" — their cue sheet, saved as waypoints.
   Re-importing those into RWGPS produces POIs with those names *in addition*
   to the cue sheet RWGPS regenerates when it snaps the track to roads. The
   result on the Edge is a POI list full of "Right".

2. THIN THE TOILETS. Course points on a Garmin course are a shared, capped
   budget: turn cues and POIs compete for the same limit, and a course that
   exceeds it gets silently truncated — losing turn cues, which matter more
   than a toilet. Day 7 alone had 113 service POIs before any cues were added.
   Water is left untouched (it is the scarce thing and there are far fewer of
   them); toilets are thinned to one per TOILET_MIN_SPACING_MI.

3. NAME CONSISTENTLY. RWGPS titles an imported route from <metadata><name>,
   and these were inconsistent — "D31 Alt Monterey to King City", "King City
   to Paso Robles" with no day number, and Day 5's metadata and track names
   disagreeing. In a library of ten routes that sorts badly and reads worse.

4. MARK START AND FINISH. A named waypoint at each end gives an obvious
   anchor on the map page and on the device.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_data  # noqa: E402  (needs the path insert above)

ROOT = Path(__file__).resolve().parent.parent
GPX_DIR = ROOT / "gpx"

NS_URI = "http://www.topografix.com/GPX/1/1"
NS = "{%s}" % NS_URI

TURN_CUE_TYPES = {"Dot"}
TOILET_MIN_SPACING_MI = 2.0
# Garmin's own symbol names, so the device shows a real icon rather than a
# generic pin. RWGPS passes <sym> through on export to a Garmin course.
START_SYM, END_SYM = "Flag, Blue", "Flag, Green"


def haversine_mi(a, b):
    return build_data.haversine_m(a, b) * build_data.M_TO_MI


def route_title(day, filename):
    """"Day N: Start to End", plus the option label when a day has several
    candidate tracks, so they are distinguishable in an RWGPS library."""
    towns = build_data.TOWNS[day]
    base = f"Day {day}: {towns['start']} to {towns['end']}"
    for opt in build_data.ROUTE_OPTIONS.get(day, []):
        if opt["file"] == filename and opt["file"] != build_data.GPX_FILES[day]:
            return f"{base} ({opt['label']})"
    return base


def set_text(parent, tag, text):
    el = parent.find(NS + tag)
    if el is None:
        el = ET.SubElement(parent, NS + tag)
    el.text = text


def thin_toilets(wpts, trkpts):
    """Keep water and landmarks; drop toilets closer than the spacing floor to
    one already kept. Order along the track is approximated by the nearest
    track point, which is enough for a spacing rule."""
    kept, kept_toilets = [], []
    for w in wpts:
        if (w.findtext(NS + "type") or "") != "Toilets":
            kept.append(w)
            continue
        here = (float(w.get("lat")), float(w.get("lon")))
        if any(haversine_mi(here, prev) < TOILET_MIN_SPACING_MI for prev in kept_toilets):
            continue
        kept_toilets.append(here)
        kept.append(w)
    return kept


def tidy(path, day):
    ET.register_namespace("", NS_URI)
    tree = ET.parse(path)
    root = tree.getroot()

    trkpts = list(root.iter(NS + "trkpt"))
    if not trkpts:
        print(f"  {path.name}: no track points, skipped")
        return

    wpts = root.findall(NS + "wpt")
    before = len(wpts)

    # 1. Drop RWGPS turn cues.
    wpts = [w for w in wpts if (w.findtext(NS + "type") or "") not in TURN_CUE_TYPES]
    cues_dropped = before - len(wpts)

    # 2. Thin toilets.
    n_before_thin = len(wpts)
    wpts = thin_toilets(wpts, trkpts)
    thinned = n_before_thin - len(wpts)

    # 4. Start/finish markers — rebuilt each run so re-running cannot stack them.
    wpts = [w for w in wpts if (w.findtext(NS + "type") or "") not in ("Start", "Finish")]
    towns = build_data.TOWNS[day]
    for label, kind, sym, pt in (
        (f"START — {towns['start']}", "Start", START_SYM, trkpts[0]),
        (f"FINISH — {towns['end']}", "Finish", END_SYM, trkpts[-1]),
    ):
        w = ET.Element(NS + "wpt", {"lat": pt.get("lat"), "lon": pt.get("lon")})
        ET.SubElement(w, NS + "name").text = label
        ET.SubElement(w, NS + "sym").text = sym
        ET.SubElement(w, NS + "type").text = kind
        wpts.insert(0 if kind == "Start" else len(wpts), w)

    for w in root.findall(NS + "wpt"):
        root.remove(w)
    trk = root.find(NS + "trk")
    at = list(root).index(trk) if trk is not None else len(list(root))
    for w in wpts:
        root.insert(at, w)
        at += 1

    # 3. Consistent naming, on both the metadata and the track.
    title = route_title(day, path.name)
    md = root.find(NS + "metadata")
    if md is None:
        md = ET.Element(NS + "metadata")
        root.insert(0, md)
    set_text(md, "name", title)
    if trk is not None:
        set_text(trk, "name", title)

    tree.write(path, encoding="UTF-8", xml_declaration=True)
    print(f"  {path.name:<50} cues -{cues_dropped:<3} toilets -{thinned:<3} "
          f"wpts {before}->{len(wpts)}  \"{title}\"")


def main():
    days = [int(a) for a in sys.argv[1:]] or list(range(1, 9))
    print("tidying GPX for Ride with GPS / Garmin:")
    for day in days:
        files = {build_data.GPX_FILES[day]}
        files.update(o["file"] for o in build_data.ROUTE_OPTIONS.get(day, []))
        for name in sorted(files):
            tidy(GPX_DIR / name, day)


if __name__ == "__main__":
    main()
