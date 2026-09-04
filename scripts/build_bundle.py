#!/usr/bin/env python3
"""Bundle the eight day tracks into two extra downloads.

    python3 scripts/build_bundle.py

Writes into gpx/, so both are published by the ordinary deploy:

  Pacific-Coast-2026-all-days.zip   the eight day files, unchanged
  Pacific-Coast-2026-all-days.gpx   one file, eight <trk> elements

The combined file keeps the days as separate tracks rather than welding them
into one 480-mile line. They do chain end to end, so a single track would be
geometrically honest, but a head unit would then offer one course you cannot
start on day 5 of. Eight tracks in one file loads as eight courses.

Every waypoint comes across too, which is the thing to watch: it is a few
hundred in one file, and some units cap what they will hold. The per-day files
are the answer if a device baulks - that is why the zip exists.

Derived from gpx/Day-*.gpx, so rerun it after anything that rewrites those:
add_services.py, prepare_gpx.py, or a new track.
"""
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_data as bd

ROOT = Path(__file__).resolve().parent.parent
GPX_DIR = ROOT / "gpx"
NS_URI = "http://www.topografix.com/GPX/1/1"
NS = f"{{{NS_URI}}}"
ZIP_NAME = "Pacific-Coast-2026-all-days.zip"
GPX_NAME = "Pacific-Coast-2026-all-days.gpx"
TITLE = "Pacific Coast Bike Tour 2026 — Los Altos to Los Angeles"


def day_files():
    """The eight day tracks, in ride order, from build_data's own mapping so
    this cannot drift from what the site publishes."""
    out = []
    for n in sorted(bd.GPX_FILES):
        p = GPX_DIR / bd.GPX_FILES[n]
        if not p.exists():
            sys.exit(f"missing: {p}")
        out.append((n, p))
    return out


def main():
    ET.register_namespace("", NS_URI)
    days = day_files()

    with zipfile.ZipFile(GPX_DIR / ZIP_NAME, "w", zipfile.ZIP_DEFLATED) as z:
        for _, p in days:
            z.write(p, arcname=p.name)
    zkb = (GPX_DIR / ZIP_NAME).stat().st_size / 1024
    print(f"{ZIP_NAME}: {len(days)} files, {zkb:.0f} KB")

    gpx = ET.Element(f"{NS}gpx", {"version": "1.1", "creator": "pacific-coast-2026"})
    meta = ET.SubElement(gpx, f"{NS}metadata")
    ET.SubElement(meta, f"{NS}name").text = TITLE
    wpts = trks = pts = 0
    for n, p in days:
        root = ET.parse(p).getroot()
        for w in root.findall(f"{NS}wpt"):
            gpx.append(w); wpts += 1
        for t in root.findall(f"{NS}trk"):
            gpx.append(t); trks += 1
            pts += len(t.findall(f".//{NS}trkpt"))
    ET.ElementTree(gpx).write(GPX_DIR / GPX_NAME, encoding="UTF-8",
                              xml_declaration=True)
    gkb = (GPX_DIR / GPX_NAME).stat().st_size / 1024
    print(f"{GPX_NAME}: {trks} tracks, {pts} points, {wpts} waypoints, {gkb:.0f} KB")


if __name__ == "__main__":
    main()
