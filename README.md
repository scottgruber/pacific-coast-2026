# Pacific Coast Bike Tour — Los Altos to Los Angeles

A day-by-day planning site for an 8-day bike tour down the California
coast, September 5–12, 2026: route maps, elevation profiles, downloadable
GPX, live conditions, afternoon shade estimates, and a page comparing the
planned route against the Adventure Cycling reference track it's loosely
based on.

Unlike a post-trip recap, this ride hasn't happened yet — there's no
recorded moving time or speed, so day pages show what a route file and a
few public data sources can tell you ahead of time: distance, elevation
gain and loss, notable climbs, where the water and food are, and how much
of the day is actually in shade.

It's a static site — Python scripts render Jinja templates + JSON data into
plain HTML in `build/`. The one exception is `api/airnow.php`, a small proxy
that exists because an AirNow key cannot be domain-restricted; everything
else is static files.

The site's own [colophon page](templates/colophon.html.jinja) explains where
each number comes from, for readers rather than maintainers.

## File tree

```
.
├── gpx/                     Day-N-*.gpx tracks, plus the
│   │                         Pacific-Coast-Section-4-SF-SB-Southbound.gpx
│   │                         reference track this route is loosely based on
│   └── fire-hazard/         the superseded Big Sur coast tracks for days 3-5,
│                             kept in case the road reopens
├── api/
│   └── airnow.php           air-quality proxy — the only server-side code on
│                             the site; see "Conditions data" for why
├── css/
│   └── main.css             all site styles, one file — color/type tokens
│                             live in :root, everything else references them
├── data/
│   ├── day-N.json           stats, elevation profile, route, services
│   │                         (built by build_data.py from gpx/)
│   ├── reference.json       same, for the Section 4 reference track
│   ├── overview.json        whole-trip summary (built by build_data.py)
│   ├── shade.json           afternoon shade per day (build_shade.py)
│   ├── towns.json           towns each day passes through (build_towns.py)
│   ├── roster.json          riders + SAG crew          — HAND-MAINTAINED
│   ├── notes.json           per-day highlights/cautions — HAND-MAINTAINED
│   └── manual-pois.json     stops OSM misses            — HAND-MAINTAINED
├── js/
│   ├── config.js            Mapbox public token (see "Maps and the Mapbox token")
│   ├── basemap.js           shared Leaflet basemaps, layer switcher, map handoff
│   ├── map.js               route map for the day pages and the overview
│   ├── compare.js           planned-vs-reference toggle map
│   ├── conditions.js        live air quality + weather on the day pages
│   └── units.js             imperial/metric toggle
├── fonts/                   self-hosted Tofino Variable / Tofino Text
│                             Variable licensed font files
├── scripts/
│   ├── build_data.py        gpx/ → data/day-N.json + reference + overview
│   ├── generate_pages.py    templates/ + data/ → build/*.html
│   ├── add_services.py      OSM water/toilets/food/shops/scenic → GPX waypoints
│   ├── prepare_gpx.py       ready the GPX for Ride with GPS and a Garmin
│   ├── build_shade.py       afternoon shade: terrain horizon + mapped canopy
│   └── build_towns.py       towns along each route
├── templates/
│   ├── _nav.html.jinja      shared site nav
│   ├── _units.html.jinja    imperial/metric macro
│   ├── _icons.html.jinja    inline SVG icons
│   ├── day.html.jinja       one per day page
│   ├── index.html.jinja     overview page (stats, day grid, roster)
│   ├── compare.html.jinja   planned-vs-reference route toggle map
│   └── colophon.html.jinja  how the site is built, and where the data is from
├── build/                   GENERATED — gitignored, see "Building" below
├── .env                     API keys — gitignored, never committed
├── icon.svg
└── README.md
```

`build/` isn't committed — it's fully regeneratable and holds the actual
deployable output: the rendered HTML plus symlinks (`css`, `js`, `fonts`,
`gpx`, `api`, `icon.svg`) back to the real files above it, so it's a
self-contained folder you can serve from any path.

Three files under `data/` are hand-maintained and never written by a script:
`roster.json`, `notes.json` and `manual-pois.json`. Edits there survive any
rebuild.

## Building

The two that run every time:

```bash
python3 scripts/build_data.py      # only if a GPX track changed
python3 scripts/generate_pages.py  # always — renders templates + data → build/
```

In practice, editing a template or `css/main.css` only needs the last step.

The other four hit live APIs, are slow, and are rate-limited, so they are run
by hand when their inputs change rather than on every build. Their output is
committed, so a normal rebuild never needs them:

```bash
python3 scripts/add_services.py    # after a GPX route changes, or manual-pois.json
python3 scripts/prepare_gpx.py     # always after add_services.py
python3 scripts/build_shade.py     # after a route changes
python3 scripts/build_towns.py     # after a route changes
```

Order matters for the first two: `add_services.py` rewrites the waypoints in
the GPX files, and `prepare_gpx.py` then strips turn cues, names the routes
consistently and adds start/finish markers. Run `build_data.py` after either,
since both change what the day JSON is built from.

`build_shade.py` caches elevations to `data/.elevation-cache.json`
(gitignored), so re-runs are cheap. Overpass and Open-Meteo are free shared
services and will rate-limit a burst; all four scripts back off and retry.

## Local preview

```bash
php -S 127.0.0.1:8744 -t build
```

then open `http://localhost:8744/`. This is also what `.claude/launch.json`'s
`static-server` config runs.

Use PHP's built-in server, not `python3 -m http.server`. A static server
hands `api/airnow.php` to the browser as source text instead of executing
it, so the conditions panel silently falls back to its links.

Map tiles will not load locally if the Mapbox token is restricted to the
live domain — add `http://localhost:*/*` to the token's URL restrictions if
you want a basemap while previewing. Everything else works without it.

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

## Route notes and hand-picked stops

Two files under `data/` hold judgement rather than measurement, and no script
ever writes to them:

`notes.json` — per-day "worth looking up for" and "what to watch for" lines,
shown at the top of each day page. Every figure quoted in there traces back to
`data/day-N.json` or `data/shade.json`, so re-check them if a route changes.
The claims about traffic, road surface and scenery do **not** come from the
data: OpenStreetMap has no shoulder, speed-limit or width tag on most of these
roads. Those come from people who have ridden them, and lines nobody has
verified say so outright rather than implying someone checked.

`manual-pois.json` — stops that OpenStreetMap misses or tags in a way
`add_services.py` does not match. Los Olivos, for instance, has well-known
places to eat and none of them are tagged as any amenity the query looks for.
Entries here are merged into the GPX on every `add_services.py` run and are
exempt from thinning; their waypoint comments say "Added by hand" so a
deliberate pick is distinguishable from a scraped one on the device.

```json
{ "6": [ { "name": "...", "lat": 0.0, "lon": 0.0,
           "type": "Food", "sym": "Restaurant", "note": "..." } ] }
```

`type` must be one of Water, Toilets, Food, Store, Winery, Picnic, Scenic or
Historic. `sym` is a Garmin symbol name.

### Removing a stop

No filter can tell you whether somewhere is worth stopping at. OSM will happily
report a rock formation as a tourist attraction, a bare place-name marker as a
historic site, and a roadside memorial to a named individual as somewhere to
visit. `add_services.py` screens out what it can — see "Stops" below — but the
rest needs a human who has looked.

Add the name under `exclude` in the same file, keyed by day. Matched
case-insensitively against the label shown on the page:

```json
{
  "exclude": {
    "5": ["MB"],
    "7": ["Jude Martin Keefer", "Lil' Toot Boat Tours"]
  }
}
```

### Applying changes

After editing either section:

```bash
python3 scripts/add_services.py 5 6   # day numbers optional; omit for all
python3 scripts/prepare_gpx.py 5 6
python3 scripts/build_data.py
python3 scripts/generate_pages.py
```

Pass day numbers to spare the Overpass rate limit when only one day changed.

To see what a day currently lists:

```bash
python3 -c "import json;d=json.load(open('data/day-5.json'));[print(f\"{s['type']:<9} mi {s['mile']:5.1f}  {s['label']}\") for s in d['services']]"
```

### What the filters already screen out

- Toilets with restricted access, and any operated by a school, college,
  church or club.
- Scenic, historic and picnic entries with no positive evidence they are
  somewhere you can stop — they need a Wikipedia or Wikidata entry, or to be a
  park or museum, or to have facilities such as benches, water or opening
  hours. When in doubt they are left out.
- Anything more than a quarter mile off the route.
- Clusters: one stop per category per 1.5–2.5 miles, keeping the most useful
  member. Water is thinned only at half a mile, so no isolated source is lost.

Stops with generic names ("Picnic", "Toilets") link to their coordinate rather
than a map search. A search for a common word can resolve somewhere else
entirely — this is how a "Picnic" link on day 5 once opened fifty miles up the
coast.

## Shade

`build_shade.py` estimates how much of each route is shaded in the afternoon,
by two independent mechanisms reported separately: mapped woodland within 30 m
of the road, and terrain high enough to block the sun. The second is the honest
form of "aspect" — it samples ground elevation along the sun's bearing out to
3.2 km and compares the largest angle subtended against the sun's own
elevation, because a west-facing slope only helps if it is steep enough and
close enough to actually block anything.

Terrain shade comes out at 0% almost everywhere at 3pm, which is a real result
rather than a broken model: the September sun is still around 49° up. Sweeping
the hour confirms the model responds — day 3 reaches 18% terrain shade by 6pm
and 50% by 7pm. During riding hours on this route, canopy is the only shade.

Canopy tests proximity, not containment. Mappers draw woodland up to the road
edge and stop, so a point on the centreline is essentially never *inside* a
wood polygon; an earlier containment test scored every day 0% while polygons
sat twenty feet away.

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
published tree as a real file.

The key is **not** in this repo and must never be. Put it on the server by
hand, once, in a file **one directory above the web root** so it cannot be
requested over HTTP, readable only by the user PHP runs as:

```bash
# on the server, outside any git repo, adjust for your own paths
printf 'AIRNOW_API_KEY=%s\n' 'your-key' > "$(dirname "$WEB_ROOT")/airnow.env"
```

`airnow.php` resolves the key in this order: the `AIRNOW_API_KEY` environment
variable (set it in the php-fpm pool or vhost if you prefer), then that file
above the web root, then the repo's gitignored `.env` — the last of which is
for local development only and must never resolve on a server. The deploy
rsyncs into a public repo, so a `.env` inside the published tree would be
committed and served. Nothing puts it there; keep it that way.

After deploying, confirm the key file is not reachable over HTTP — requesting
it should 404, not 200.

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
