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
├── gpx/                    Day-N-*.gpx tracks plus the
│                            Pacific-Coast-Section-4-SF-SB-Southbound.gpx
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

## Maps and the Mapbox token

Basemaps come from Mapbox as raster tiles (the Static Tiles API), driven by
`js/basemap.js` — one module shared by the day pages, the overview map and the
compare page, so the tile source and the layer switcher are defined once. Three
styles are offered, because the two things this route actually needs checking
are shade and climbing:

| Layer | Mapbox style | What it's for |
| --- | --- | --- |
| Plain | `mapbox/light-v11` | reading town and road names |
| Vegetation | `mapbox/satellite-streets-v12` | tree cover, visible directly in imagery |
| Elevation | `mapbox/outdoors-v12` | contour lines and hillshade |

Clicking anywhere on a map drops a pin and offers that point to Google Maps,
Apple Maps or Street View. Neither Google nor Apple lets a third-party site
embed an interactive map or Street View without a paid, billed key, so handing
the coordinate off is the practical substitute.

### The token

The public token lives in `js/config.js` as `MAPBOX_TOKEN`. It must be a
**public** token (starts with `pk.`), restricted to this site's domain under
Account → Tokens → URL restrictions in Mapbox. A public token is visible in
page source by design; the URL restriction is what protects it.

Never put a **secret** token (`sk.`) in this repo. Secret tokens cannot be
URL-restricted, carry account-management scopes, and are for server-side/CLI
use only. A public token needs only the default `styles:tiles`, `styles:read`
and `fonts:read` scopes — `styles:list` / `fonts:list` are for enumerating an
account's styles and are not used for rendering.

With the token left empty, the maps fall back to CARTO's keyless tiles, so the
site still builds and renders — just on the rate-limited basemap with no layer
switcher.

## Route options

`ROUTE_OPTIONS` in `scripts/build_data.py` lists candidate tracks for days
where the route isn't settled. The file named in `GPX_FILES` is the primary —
it drives the day's headline stats and elevation chart, and draws as the solid
line. Everything else in `ROUTE_OPTIONS` draws dashed and gets its own download
button and distance/climbing summary, so the options can be compared before one
is committed to. Day 3 currently carries two.

Superseded tracks (the original Big Sur coast routing for days 3–5, closed by
fire) are kept in `gpx/fire-hazard/` rather than deleted, in case the coast
reopens before the trip.

On a day with options, the page shows a radio switcher. Selecting one makes its
line solid and on top, dashes the others, and swaps the elevation chart — and
nothing else. The stat row, the climb list and the whole-trip totals on the
index always reflect the primary, because selection is a **preview**, not a
decision. Committing to a route means changing `GPX_FILES` and rebuilding; the
"Current pick" badge, not the current selection, marks which one that is. A
preview note under an alternate's chart says so explicitly, so a chart from one
route is never read against numbers from another.

## Cache busting

`generate_pages.py` exposes an `asset()` helper to the templates, which appends
a short content hash to every local `css/` and `js/` URL
(`js/config.js?v=81974b1363`). Returning visitors were otherwise being served
stale JavaScript after a deploy — a new Mapbox token in `config.js` went unseen
until a hard reload, and the map silently fell back to CARTO tiles. The hash
changes only when a file's bytes change, so unchanged assets stay cached.

Reference assets through `asset()` in templates, never as a bare path, or they
will not be busted.

## Conditions data

Each day page shows live air quality and weather for where the day finishes,
fetched on load by `js/conditions.js`. Both degrade safely: the card ships with
working external links, and each section is only replaced once its own fetch
succeeds, so a failure or a slow network leaves the links rather than blanking
the panel.

**Weather** comes straight from the National Weather Service
(`api.weather.gov`) — no key, CORS open, so it stays client-side. It takes
three hops: the grid point for the coordinate, that grid's forecast, then the
nearest station's latest observation (the forecast payload carries no
humidity).

**Air quality** goes through `api/airnow.php`. It does not call AirNow from the
browser, and must not: unlike a Mapbox public token, an AirNow key cannot be
restricted to a domain, and its rate limit is enforced per key. A published key
lets anyone drain the hourly quota, after which AirNow returns nothing until the
next hour.

Every reading names the reporting station and its distance from the day's
endpoint. That is deliberate, not decoration — AirNow's network is sparse
inland along this route (Monterey has no station within 75 miles at all, so
Day 2 falls back to its link), and smoke varies sharply over short distances. A
bare AQI number would imply a precision the reading does not have. Readings from
more than 20 miles away are flagged in the UI as a regional signal only.

The proxy caches for 10 minutes per rounded coordinate, refuses coordinates
outside the route's bounding box (so it can't be used as a general-purpose AQI
proxy on this key), and — if AirNow is unreachable or rate-limiting — re-serves
the last cached reading flagged `stale`, which the page labels with its age.
Showing a reading a few minutes old beats an empty panel when someone is
checking for smoke.

### Deploying the proxy

`api/` is symlinked into `build/`, so `rsync -aL` copies `airnow.php` into the
published tree as a real file. The web root is
`/var/www/scottgruber.me/html`, so put the key **one level above it**, by hand,
once — never through git:

```bash
# on the server, NOT in any git repo
echo 'AIRNOW_API_KEY=<your-key>' > /var/www/scottgruber.me/airnow.env
chmod 600 /var/www/scottgruber.me/airnow.env
```

`airnow.php` resolves the key in this order: the `AIRNOW_API_KEY` environment
variable (if you would rather set it in the php-fpm pool or vhost), then
`dirname(DOCUMENT_ROOT) . '/airnow.env'` as above, then the repo's gitignored
`.env` — that last one is for local development only and must never resolve on
the server. The deploy rsyncs into a public git repo, so a `.env` inside the
published tree would be committed and served over HTTP. Nothing puts it there
today; keep it that way.

Local preview runs `php -S` (see `.claude/launch.json`) rather than
`python3 -m http.server`, so the proxy actually executes. A static server would
serve `airnow.php` as source text instead of running it.

### Not yet pulled in

| Layer | Source | Notes |
| --- | --- | --- |
| Tree cover / shade | Sentinel-2 NDVI, or the USFS Tree Canopy Cover raster | sample the raster along the track; NDVI needs a cloud-free scene |
| Afternoon aspect | derived, not fetched | compute bearing per track segment and cross it with a DEM (USGS 3DEP, or Mapbox Terrain-RGB tiles, which the token already covers) — a westward slope in late afternoon is the exposed case |

Neither is a live feed. Aspect in particular is a computation: it falls out of
the GPX plus an elevation raster, so it could be precomputed in
`build_data.py` and baked into `data/day-N.json` exactly like the climb
detection already is — which would let the two Day 3 alternates be compared on
shade with numbers rather than by eye.
