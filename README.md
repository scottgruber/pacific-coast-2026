# Pacific Coast Bike Tour — Los Altos to Los Angeles

A day-by-day planning site for an 8-day bike tour down the California
coast, September 5–12, 2026: route maps, elevation profiles, and a page
comparing our planned route against the Adventure Cycling reference track
it's loosely based on.

Unlike a post-trip recap, this ride hasn't happened yet — there's no
recorded moving time or speed, so day pages show what a route file can
tell you ahead of time: distance, elevation gain/loss, and notable climbs.

It's a static site — Python scripts render Jinja templates + JSON data into
plain HTML in `build/`. No server-side code runs on the deployed site.

## File tree

```
.
├── gpx/                    Day-N-*.gpx tracks (Day 6 not mapped yet) plus
│                            the Pacific-Coast-Section-4-SF-SB-Southbound.gpx
│                            reference track it's loosely based on
├── css/
│   └── main.css             all site styles, one file — color/type tokens
│                             live in :root, everything else references them
├── data/
│   ├── day-N.json            stats, elevation profile, route, waypoints
│   │                         (built by build_data.py from gpx/)
│   ├── reference.json        same, for the Section 4 reference track
│   ├── overview.json         whole-trip summary (built by build_data.py)
│   └── roster.json           riders + SAG crew (hand-maintained, not GPX-derived)
├── js/
│   ├── map.js                Leaflet route map (day pages + overview)
│   ├── compare.js             two-layer toggleable map for compare.html
│   └── units.js              imperial/metric toggle
├── fonts/                    self-hosted Tofino Variable / Tofino Text
│                              Variable licensed font files
├── scripts/
│   ├── build_data.py         GPX → data/day-N.json + reference.json +
│   │                         overview.json
│   └── generate_pages.py     templates/ + data/ → build/*.html
├── templates/
│   ├── _nav.html.jinja       shared site nav
│   ├── _units.html.jinja     imperial/metric macro
│   ├── day.html.jinja        one per day page
│   ├── index.html.jinja      overview page (stats, day grid, roster)
│   └── compare.html.jinja    planned-vs-reference route toggle map
├── build/                    GENERATED — gitignored, see "Building" below
├── icon.svg
└── README.md
```

`build/` isn't committed — it's fully regeneratable and holds the actual
deployable output: the rendered HTML plus symlinks (`css`, `js`, `icon.svg`)
back to the real files above it, so it's a self-contained folder you can
serve from any path.

## Building

Full rebuild, in order:

```bash
python3 scripts/build_data.py      # only if a GPX track changed
python3 scripts/generate_pages.py  # always — renders templates + data → build/
```

In practice, editing a template or `css/main.css` only needs the last step.

## Local preview

```bash
python3 -m http.server 8744 --directory build
```

then open `http://localhost:8744/`. This is also what `.claude/launch.json`'s
`static-server` config runs.

## Version control

This repo tracks all of the source: templates, scripts, `css/`, `js/`,
`data/*.json`, `gpx/*.gpx`, `fonts/`, and `icon.svg`. None of it is large
enough to warrant exclusion — this mirrors the pattern used by the
`huandao` site (another static Jinja site of this kind), which only
gitignores things too big to be worth git history (video files). `build/`
is gitignored here since it's fully regeneratable output, never a source
of truth.

Remote: `git@github.com:scottgruber/pacific-coast-2026.git`

## Deploying to scottgruber.me/bike-tours/pacific-coast/2026

This repo is the source of truth (templates, scripts, data, gpx tracks).
`scottgruber.me/bike-tours/pacific-coast/2026/` is a plain copy of `build/`'s
output, published inside the `scottgruber.me` git repo, which is deployed by
sshing into the server and running `git pull` — the same pattern `huandao`
uses for `scottgruber.me/bike-tours/taiwan-huandao/2023`. (Previously this
lived at `scottgruber.me/bike-tour/pacific-coast/2026` — singular — renamed
to the plural `bike-tours/<trip>/<year>` convention used going forward.)

**1. Regenerate the build:**

```bash
cd ~/Sites/pacific-coast
python3 scripts/generate_pages.py
```

**2. Copy `build/`'s output (resolving the symlinks) into the scottgruber.me repo:**

```bash
mkdir -p ~/Sites/scottgruber.me/bike-tours/pacific-coast/2026
rsync -aL build/ ~/Sites/scottgruber.me/bike-tours/pacific-coast/2026/
```

(`-L` follows the symlinks so `css`/`js`/`fonts`/`icon.svg` get copied as
real files rather than links pointing outside the scottgruber.me repo.)

**3. Commit and push:**

```bash
cd ~/Sites/scottgruber.me
git add bike-tours/pacific-coast
git commit -m "Update Pacific Coast Bike Tour site"
git push
```

**4. Pull on the server:**

```bash
ssh <your-server>
cd <path-to-scottgruber.me>
git pull
exit
```

Steps 2–4 only need repeating when the built HTML or its source assets
actually changed.

## Day 6

Oceano to Santa Barbara doesn't have a GPX track yet — `build_data.py`
writes it as a pending placeholder (town names only), and both the day
page and the day-grid card on the overview show a "route not mapped yet"
state instead of stats. Once `gpx/Day-6-Oceano-to-Santa-Barbara.gpx` exists,
add it to `GPX_FILES` in `scripts/build_data.py` and rebuild.

## Roster

Rider names and the SAG car crew live in `data/roster.json` — they aren't
derivable from the GPX tracks, so they're hand-maintained there rather than
computed by `build_data.py`. `generate_pages.py` reads it directly.

## Retheming

The placeholder ocean/coastline palette lives entirely in the `:root`
tokens at the top of `css/main.css` (`--color-background`, `--color-theme`,
`--color-title`, etc.), plus `--color-route-planned` / `--color-route-reference`
for the two lines on the compare-page map — `js/compare.js` reads those two
at runtime via `getComputedStyle`, so changing them in CSS recolors the map
too. No other file hardcodes color.
