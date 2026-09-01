#!/usr/bin/env python3
"""Render templates/*.jinja + data/*.json into static day-N.html / index.html
/ compare.html. Run after build_data.py (or after editing a template).

Output goes to build/ — a self-contained, directly-servable copy (HTML plus
symlinks to css/, js/, and the icon) so `python3 -m http.server --directory
build` previews exactly what gets deployed, at any subpath. build/ itself is
gitignored and fully regeneratable from source.
"""
import hashlib
import json
import math
import os
import shutil
import urllib.parse
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TEMPLATES_DIR = ROOT / "templates"
BUILD_DIR = ROOT / "build"

MI_TO_KM = 1.609344


def ensure_build_symlinks():
    """(Re)create build/'s symlinks back to the real, hand-edited/generated
    assets at the repo root, so build/ never holds its own stale copies."""
    BUILD_DIR.mkdir(exist_ok=True)
    links = {
        "css": "../css",
        "js": "../js",
        "fonts": "../fonts",
        "gpx": "../gpx",
        "api": "../api",
        "icon.svg": "../icon.svg",
    }
    for name, target in links.items():
        link = BUILD_DIR / name
        if link.is_symlink() and os.readlink(link) == target:
            continue
        if link.is_symlink() or link.is_file():
            link.unlink()
        elif link.is_dir():
            shutil.rmtree(link)
        os.symlink(target, link)


def mi_to_km(mi):
    return round(mi * MI_TO_KM, 1)


def with_km(d, mi_key, km_key):
    d[km_key] = mi_to_km(d[mi_key])


def with_m(d, ft_key, m_key):
    d[m_key] = round(d[ft_key] / 3.28084)


MI_TO_M = MI_TO_KM * 1000.0

# How many times steeper than true 1:1 mi/ft scale the chart is allowed to
# draw slopes. Some exaggeration is unavoidable for a road elevation profile
# to be legible at all (real coastal elevation change is tiny relative to
# miles traveled). Set above the typical 6-12x range so a climb reads as a
# clear shape at a glance — this chart's job is quick visual gut-check
# ("there's a climb here") for mental trip prep, not precise gradient-reading.
TARGET_VERTICAL_EXAGGERATION = 20
ELEVATION_CHART_WIDTH = 600
ELEVATION_CHART_PAD = 6
ELEVATION_CHART_MIN_HEIGHT = 40
ELEVATION_CHART_MAX_HEIGHT = 160


def build_elevation_svg(profile, day, shared_min_e, shared_max_e):
    """Render the elevation sparkline against a shared elevation scale
    (shared_min_e..shared_max_e, in meters) common to every day page, rather
    than each day auto-fitting its own min/max to a fixed chart height. That
    auto-fit was the source of wildly inconsistent, misleadingly steep-
    looking profiles: a day with a small elevation range got stretched just
    as tall as the hilliest day. With a shared Y-axis domain, a flat day
    actually looks flat, and only the real outlier climbs stand out.

    The X axis still auto-fits each day's own distance to a fixed chart
    width (a shorter day's chart still spans the full width) — only the
    elevation domain is shared. Holding both the elevation domain AND the
    chart height fixed across every day would let vertical exaggeration
    balloon on the longer days (the more real miles compressed into the
    same width, the steeper a fixed-height chart draws every slope) — up
    around 20-40x for this route, which is exactly the "spike chart"
    problem this replaces. Instead, height is solved for per day so every
    chart renders at the *same* TARGET_VERTICAL_EXAGGERATION: longer days
    get a shorter, wider-looking band; shorter days get a taller one. That
    keeps relative steepness comparable across days without secretly
    varying how exaggerated each one is.
    """
    dists = [p["d_mi"] for p in profile]
    eles = [p["ele_m"] for p in profile]
    min_e, max_e = min(eles), max(eles)
    span_e = max(shared_max_e - shared_min_e, 1.0)
    max_d = max(dists) or 1.0
    max_d_m = max_d * MI_TO_M

    width, pad = ELEVATION_CHART_WIDTH, ELEVATION_CHART_PAD
    x_px_per_m = (width - 2 * pad) / max_d_m
    y_px_per_m = TARGET_VERTICAL_EXAGGERATION * x_px_per_m
    height = 2 * pad + y_px_per_m * span_e
    height = max(ELEVATION_CHART_MIN_HEIGHT, min(ELEVATION_CHART_MAX_HEIGHT, height))
    height = round(height)

    def px(d):
        return pad + (d / max_d) * (width - 2 * pad)

    def py(e):
        return (height - pad) - ((e - shared_min_e) / span_e) * (height - 2 * pad)

    pts = [(px(d), py(e)) for d, e in zip(dists, eles)]
    line = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = line + f" L{pts[-1][0]:.1f},{height - pad} L{pts[0][0]:.1f},{height - pad} Z"

    svg = (
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        f'aria-labelledby="elev-title-{day} elev-desc-{day}">'
        f'<title id="elev-title-{day}">Elevation profile for Day {day}</title>'
        f'<desc id="elev-desc-{day}">Ranges from {min_e:.0f}m to {max_e:.0f}m '
        f'elevation over {max_d:.1f} miles, plotted on a shared '
        f'{shared_min_e:.0f}–{shared_max_e:.0f}m scale used for every day.</desc>'
        f'<path d="{area}" fill="var(--color-accent-rust)" fill-opacity="0.18" stroke="none"/>'
        f'<path d="{line}" fill="none" stroke="var(--color-accent-rust)" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f"</svg>"
    )

    # Recompute the *actual* exaggeration from the final (clamped, rounded)
    # height, in case the min/max height clamp ever kicks in — should equal
    # TARGET_VERTICAL_EXAGGERATION exactly under normal circumstances.
    actual_y_px_per_m = (height - 2 * pad) / span_e
    exaggeration = round(actual_y_px_per_m / x_px_per_m)

    return {"svg": svg, "exaggeration": exaggeration, "height": height}


# Cache busting -----------------------------------------------------------
#
# Returning visitors were being served stale css/js after a deploy — the HTML
# revalidates but the assets sat in browser cache, so a new Mapbox token in
# config.js went unseen until a hard reload. Appending a content hash gives
# each asset a new URL whenever its bytes change, so browsers fetch the new
# file automatically and keep caching the unchanged ones.
_asset_versions = {}


def asset(rel):
    """Return `rel` with a ?v=<content hash> suffix for cache busting."""
    if rel not in _asset_versions:
        f = ROOT / rel
        _asset_versions[rel] = (
            hashlib.sha256(f.read_bytes()).hexdigest()[:10] if f.exists() else ""
        )
    v = _asset_versions[rel]
    return f"{rel}?v={v}" if v else rel


# Order the stops list by what a rider needs first, not alphabetically.
SERVICE_GROUPS = [
    ("Water", "Water"),
    ("Store", "Shops &amp; markets"),
    ("Food", "Food &amp; coffee"),
    ("Winery", "Tasting rooms &amp; bars"),
    ("Park", "Parks"),
    ("Toilets", "Restrooms"),
    ("Scenic", "Scenic"),
    ("Historic", "Historic"),
]


def map_links(label, lat, lon):
    """Google and Apple Maps URLs for a stop.

    Both take the place name as well as the coordinate, which matters: a bare
    lat/lon drops a pin on a blank spot with no name, hours, or photos, while
    the name resolves to the actual listing. Apple takes them as separate
    parameters (`q` labels the pin, `ll` places it); Google has no equivalent,
    so the name and coordinate go into one query string, which resolves to the
    POI when it is listed and falls back to the coordinate when it is not.

    A generic label ("Toilets", "Water") is no help to either, so those fall
    back to the coordinate alone rather than searching for the word."""
    ll = f"{lat:.5f},{lon:.5f}"
    # A name only helps if it identifies somewhere. Sending "Picnic" or "MB" to
    # a map search invites it to resolve the word somewhere else entirely -
    # "Picnic" once landed a reader near Big Sur, fifty miles off the route.
    # Anything generic, too short, or identical to its own category falls back
    # to the coordinate, which is always exactly right.
    #
    # The generic list is derived, not restated: an earlier hard-coded copy went
    # stale the moment a Picnic category was added.
    name = label.strip()
    generic = (
        not name
        or len(name) <= 3
        or name.lower() in {t.lower() for t, _ in SERVICE_GROUPS}
        or name.lower() in {"picnic", "picnic area", "restroom", "restrooms",
                            "drinking water", "toilet", "wc"}
    )
    if generic:
        return (f"https://www.google.com/maps/search/?api=1&query={ll}",
                f"https://maps.apple.com/?q={ll}&ll={ll}")
    q = urllib.parse.quote(name)
    return (f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(name + ' ' + ll)}",
            f"https://maps.apple.com/?q={q}&ll={ll}")


def with_map_links(lodging):
    """The night's hotel, plus map URLs. Named, so both maps resolve the
    listing rather than dropping an unlabelled pin on a car park."""
    if not lodging:
        return None
    out = dict(lodging)
    out["google"], out["apple"] = map_links(out["name"], out["lat"], out["lon"])
    return out


def group_services(services):
    """Bucket a day's POIs by type, in SERVICE_GROUPS order, each sorted by
    distance along the route. Empty groups are dropped."""
    out = []
    for key, label in SERVICE_GROUPS:
        stops = [dict(s) for s in services if s["type"] == key]
        if not stops:
            continue
        for s in stops:
            s["google"], s["apple"] = map_links(s["label"], s["lat"], s["lon"])
        # Not "items": Jinja resolves g.items to the dict's own .items method.
        out.append({"key": key.lower(), "label": label, "stops": stops})
    return out


def load_day(n):
    return json.loads((DATA_DIR / f"day-{n}.json").read_text())


def main():
    ensure_build_symlinks()

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals["asset"] = asset

    days = [load_day(n) for n in range(1, 9)]
    overview = json.loads((DATA_DIR / "overview.json").read_text())
    reference = json.loads((DATA_DIR / "reference.json").read_text())
    roster = json.loads((DATA_DIR / "roster.json").read_text())
    # Optional: written by build_shade.py, which is far too slow to run on
    # every build. Days without an entry simply omit the shade card.
    shade_path = DATA_DIR / "shade.json"
    shade = json.loads(shade_path.read_text()) if shade_path.exists() else {}
    towns_path = DATA_DIR / "towns.json"
    towns_by_day = json.loads(towns_path.read_text()) if towns_path.exists() else {}
    # Hand-maintained prose, never generated — see the note inside the file.
    notes_path = DATA_DIR / "notes.json"
    notes = json.loads(notes_path.read_text()) if notes_path.exists() else {}

    trip_start = overview["trip_start"]
    trip_end = overview["trip_end"]

    def fmt_date(iso):
        y, m, d = iso.split("-")
        months = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        return f"{months[int(m)]} {int(d)}"

    trip_dates = f"{fmt_date(trip_start)}–{fmt_date(trip_end)}, {trip_start[:4]}"

    # Shared elevation scale for every day's elevation chart — 0 up to the
    # highest point reached on any day (padded a bit, rounded to a clean
    # number), rather than each day auto-fitting to its own min/max. See
    # build_elevation_svg()'s docstring for why.
    known_days = [d for d in days if not d["pending"]]
    trip_max_m = max(d["elevation"]["max_m"] for d in known_days)
    shared_min_e = 0.0
    shared_max_e = math.ceil(trip_max_m * 1.1 / 50.0) * 50.0

    # Day pages ---------------------------------------------------------
    day_template = env.get_template("day.html.jinja")
    for d in days:
        n = d["day"]
        ctx = {
            "day": n,
            "pending": d["pending"],
            "date_label": d["date_label"],
            "towns": d["towns"],
            "prev_day": n - 1 if n > 1 else None,
            "next_day": n + 1 if n < 8 else None,
        }
        if not d["pending"]:
            stats = dict(d["stats"])
            with_km(stats, "distance_mi", "distance_km")
            elevation = dict(d["elevation"])
            with_m(elevation, "gain_ft", "gain_m")
            with_m(elevation, "loss_ft", "loss_m")
            with_m(elevation, "max_ft", "max_m")
            climbs = []
            for c in elevation["climbs"]:
                c = dict(c)
                with_m(c, "gain_ft", "gain_m")
                with_km(c, "start_mi", "start_km")
                with_km(c, "end_mi", "end_km")
                climbs.append(c)
            elevation["climbs"] = climbs
            elev_chart = build_elevation_svg(
                d["elevation"]["profile"], n, shared_min_e, shared_max_e
            )
            # Candidate tracks for days still deciding. The one matching the
            # day's primary GPX is flagged so the page can badge it and the map
            # can skip redrawing it under the main route line.
            day_shade = shade.get(str(n), {})
            options = []
            for i, o in enumerate(d.get("options", [])):
                o = dict(o)
                o["primary"] = o["gpx"] == d["gpx"]
                o["index"] = i
                with_km(o, "distance_mi", "distance_km")
                with_m(o, "gain_ft", "gain_m")
                with_m(o, "loss_ft", "loss_m")
                # Each option gets its own chart on the same shared elevation
                # scale as every other day, so switching between them compares
                # like with like rather than rescaling under the reader.
                chart = build_elevation_svg(o.pop("profile"), f"{n}-opt{i}", shared_min_e, shared_max_e)
                o["svg"] = chart["svg"]
                o["exaggeration"] = chart["exaggeration"]
                options.append(o)
            # Conditions links are anchored on where the day finishes.
            end_lat, end_lon = d["route"][-1]
            ctx.update({
                "stats": stats,
                "elevation": elevation,
                "elevation_svg": elev_chart["svg"],
                "elevation_exaggeration": elev_chart["exaggeration"],
                "gpx": d["gpx"],
                "options": options,
                "shade": day_shade.get("primary"),
                "services": group_services(d.get("services", [])),
                "lodging": with_map_links(d.get("lodging")),
                "lodging_json": json.dumps(d.get("lodging")),
                "towns_through": towns_by_day.get(str(n), []),
                "notes": notes.get(str(n)),
                "end_lat": round(end_lat, 4),
                "end_lon": round(end_lon, 4),
                "route_json": json.dumps(d["route"]),
                "towns_json": json.dumps(d["towns"]),
                "waypoints_json": json.dumps(d["waypoints"]),
                "options_json": json.dumps([
                    {k: v for k, v in o.items() if k != "svg"} for o in options
                ]),
            })
        html = day_template.render(**ctx)
        (BUILD_DIR / f"day-{n}.html").write_text(html)
        print(f"wrote day-{n}.html")

    # Index page ----------------------------------------------------------
    index_days = []
    for d in days:
        entry = {
            "day": d["day"],
            "pending": d["pending"],
            "date_label": d["date_label"],
            "towns": d["towns"],
        }
        if not d["pending"]:
            entry["distance_mi"] = d["stats"]["distance_mi"]
            entry["distance_km"] = mi_to_km(d["stats"]["distance_mi"])
            entry["gain_ft"] = d["elevation"]["gain_ft"]
            entry["gain_m"] = round(d["elevation"]["gain_ft"] / 3.28084)
        index_days.append(entry)

    map_days = [
        {"day": d["day"], "towns": d["towns"], "route": d["route"]}
        for d in days if not d["pending"]
    ]

    overview_stats = {
        "distance_mi": overview["distance_mi"],
        "distance_km": mi_to_km(overview["distance_mi"]),
        "gain_ft": overview["gain_ft"],
        "gain_m": round(overview["gain_ft"] / 3.28084),
    }

    index_template = env.get_template("index.html.jinja")
    ctx = {
        "stats": overview_stats,
        "days": index_days,
        "days_json": json.dumps(map_days),
        "total_days": overview["total_days"],
        "known_days": overview["known_days"],
        "trip_dates": trip_dates,
        "riders": roster["riders"],
        "sag": roster["sag"],
    }
    html = index_template.render(**ctx)
    (BUILD_DIR / "index.html").write_text(html)
    print("wrote index.html")

    # Compare page ----------------------------------------------------------
    planned = {
        "distance_mi": overview["distance_mi"],
        "distance_km": mi_to_km(overview["distance_mi"]),
    }
    reference_ctx = {
        "name": reference["name"],
        "distance_mi": reference["stats"]["distance_mi"],
        "distance_km": mi_to_km(reference["stats"]["distance_mi"]),
    }

    compare_template = env.get_template("compare.html.jinja")
    ctx = {
        "planned": planned,
        "reference": reference_ctx,
        "planned_days_json": json.dumps(map_days),
        "reference_route_json": json.dumps(reference["route"]),
    }
    html = compare_template.render(**ctx)
    (BUILD_DIR / "compare.html").write_text(html)
    print("wrote compare.html")

    # Colophon ------------------------------------------------------------
    # No data of its own — it documents where everything else comes from.
    colophon_template = env.get_template("colophon.html.jinja")
    (BUILD_DIR / "colophon.html").write_text(colophon_template.render())
    print("wrote colophon.html")


if __name__ == "__main__":
    main()
