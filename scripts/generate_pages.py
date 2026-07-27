#!/usr/bin/env python3
"""Render templates/*.jinja + data/*.json into static day-N.html / index.html
/ compare.html. Run after build_data.py (or after editing a template).

Output goes to build/ — a self-contained, directly-servable copy (HTML plus
symlinks to css/, js/, and the icon) so `python3 -m http.server --directory
build` previews exactly what gets deployed, at any subpath. build/ itself is
gitignored and fully regeneratable from source.
"""
import json
import os
import shutil
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


def build_elevation_svg(profile, day, width=600, height=140, pad=6):
    dists = [p["d_mi"] for p in profile]
    eles = [p["ele_m"] for p in profile]
    min_e, max_e = min(eles), max(eles)
    span_e = max(max_e - min_e, 1.0)
    max_d = max(dists) or 1.0

    def px(d):
        return pad + (d / max_d) * (width - 2 * pad)

    def py(e):
        return (height - pad) - ((e - min_e) / span_e) * (height - 2 * pad)

    pts = [(px(d), py(e)) for d, e in zip(dists, eles)]
    line = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = line + f" L{pts[-1][0]:.1f},{height - pad} L{pts[0][0]:.1f},{height - pad} Z"

    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        f'aria-labelledby="elev-title-{day} elev-desc-{day}">'
        f'<title id="elev-title-{day}">Elevation profile for Day {day}</title>'
        f'<desc id="elev-desc-{day}">Ranges from {min_e:.0f}m to {max_e:.0f}m '
        f'elevation over {max_d:.1f} miles.</desc>'
        f'<path d="{area}" fill="var(--color-accent-rust)" fill-opacity="0.18" stroke="none"/>'
        f'<path d="{line}" fill="none" stroke="var(--color-accent-rust)" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f"</svg>"
    )


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

    days = [load_day(n) for n in range(1, 9)]
    overview = json.loads((DATA_DIR / "overview.json").read_text())
    reference = json.loads((DATA_DIR / "reference.json").read_text())
    roster = json.loads((DATA_DIR / "roster.json").read_text())

    trip_start = overview["trip_start"]
    trip_end = overview["trip_end"]

    def fmt_date(iso):
        y, m, d = iso.split("-")
        months = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        return f"{months[int(m)]} {int(d)}"

    trip_dates = f"{fmt_date(trip_start)}–{fmt_date(trip_end)}, {trip_start[:4]}"

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
            ctx.update({
                "stats": stats,
                "elevation": elevation,
                "elevation_svg": build_elevation_svg(d["elevation"]["profile"], n),
                "route_json": json.dumps(d["route"]),
                "towns_json": json.dumps(d["towns"]),
                "waypoints_json": json.dumps(d["waypoints"]),
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


if __name__ == "__main__":
    main()
