<?php
/**
 * AirNow air-quality proxy.
 *
 * The site is otherwise entirely static. This exists for one reason: an AirNow
 * API key cannot be restricted to a domain the way a Mapbox public token can,
 * and its rate limit is enforced per key. Shipping the key to the browser would
 * let anyone drain the hourly quota, after which AirNow stops returning data
 * until the next hour — the conditions panel would go blank, plausibly right
 * when someone on the road is checking for smoke. So the key stays server-side
 * and the browser talks to this instead.
 *
 * Returns JSON:
 *   { aqi, parameter, category, reporting_area, distance_mi, far,
 *     observed, fetched, all[] }
 * or { error } with a non-200 status.
 *
 * Requires PHP 8.1+. No dependencies beyond ext-curl (falls back to
 * file_get_contents when allow_url_fopen is on).
 */

declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
// Let the browser and any intermediary reuse a reading for a few minutes; the
// upstream data only updates hourly.
header('Cache-Control: public, max-age=300');

/*
 * Where the key may live, in order of preference:
 *
 *   1. An environment variable, set in the php-fpm pool / Apache vhost.
 *   2. A file ABOVE the web root, put on the server by hand, once.
 *   3. The repo's gitignored .env — local development only.
 *
 * Option 3 must never resolve on the server. The deploy rsyncs build/ into a
 * public git repo, so a .env inside the deployed tree would be committed and
 * served over HTTP. It is only reachable here when running from the source
 * checkout, where api/ has not yet been flattened by rsync -L.
 */
const KEY_ENV_VAR = 'AIRNOW_API_KEY';
const PRIVATE_KEY_FILENAME = 'airnow.env';

// Requests outside the trip's corridor are refused, so the endpoint cannot be
// used as a free general-purpose AQI proxy on this key. Padded ~0.5 degrees
// around the route's actual bounding box (lat 34.01-37.39, lon -122.11..-118.50).
const BBOX = ['lat_min' => 33.5, 'lat_max' => 37.9, 'lon_min' => -122.7, 'lon_max' => -118.0];

const AIRNOW_URL = 'https://www.airnowapi.org/aq/observation/latLong/current/';
const SEARCH_RADIUS_MI = 75;
const TIMEOUT_S = 12;
const CACHE_TTL_S = 600;
// Past this, the reading is flagged so the page can say the station is too far
// away to stand in for conditions at the route. AirNow's network is sparse
// inland along this route and smoke varies sharply over short distances.
const FAR_MI = 20.0;

function fail(int $status, string $message): never
{
    http_response_code($status);
    echo json_encode(['error' => $message], JSON_UNESCAPED_SLASHES);
    exit;
}

/**
 * Last resort when the upstream call fails: re-serve the previous reading with
 * stale=true so the page can label it, or 502 if there is nothing cached.
 */
function serve_stale_or_fail(string $cacheFile): never
{
    if (is_readable($cacheFile)) {
        $cached = file_get_contents($cacheFile);
        $decoded = $cached === false ? null : json_decode($cached, true);
        if (is_array($decoded)) {
            $decoded['stale'] = true;
            $decoded['stale_age_s'] = time() - filemtime($cacheFile);
            header('X-Cache: STALE');
            echo json_encode($decoded, JSON_UNESCAPED_SLASHES);
            exit;
        }
    }
    fail(502, 'Air quality service is unavailable.');
}

/** Minimal KEY=VALUE reader, matching the .env format build_data.py expects. */
function read_env_file(string $path, string $wanted): ?string
{
    if (!is_readable($path)) {
        return null;
    }
    foreach (file($path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) as $line) {
        $line = trim($line);
        if ($line === '' || str_starts_with($line, '#') || !str_contains($line, '=')) {
            continue;
        }
        [$k, $v] = explode('=', $line, 2);
        if (trim($k) === $wanted) {
            return trim(trim(trim($v), '"'), "'");
        }
    }
    return null;
}

function api_key(): string
{
    $fromEnv = getenv(KEY_ENV_VAR);
    if (is_string($fromEnv) && $fromEnv !== '') {
        return $fromEnv;
    }
    $docRoot = $_SERVER['DOCUMENT_ROOT'] ?? '';
    if ($docRoot !== '') {
        $private = dirname($docRoot) . '/' . PRIVATE_KEY_FILENAME;
        $key = read_env_file($private, KEY_ENV_VAR);
        if ($key !== null && $key !== '') {
            return $key;
        }
    }
    // Local development only — see the note above.
    $key = read_env_file(__DIR__ . '/../.env', KEY_ENV_VAR);
    if ($key !== null && $key !== '') {
        return $key;
    }
    fail(503, 'Air quality is not configured on this server.');
}

function haversine_mi(float $lat1, float $lon1, float $lat2, float $lon2): float
{
    $r = 3958.7613; // Earth radius in miles.
    $dLat = deg2rad($lat2 - $lat1);
    $dLon = deg2rad($lon2 - $lon1);
    $a = sin($dLat / 2) ** 2
        + cos(deg2rad($lat1)) * cos(deg2rad($lat2)) * sin($dLon / 2) ** 2;
    return 2 * $r * asin(min(1.0, sqrt($a)));
}

function http_get(string $url): ?string
{
    if (function_exists('curl_init')) {
        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => TIMEOUT_S,
            CURLOPT_FOLLOWLOCATION => false,
            CURLOPT_USERAGENT => 'pacific-coast-2026/1.0 (+https://scottgruber.me)',
        ]);
        $body = curl_exec($ch);
        $status = curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
        // No curl_close(): it has been a no-op since PHP 8.0 and is deprecated
        // in 8.5, where the notice would print ahead of the JSON body.
        return ($body === false || $status !== 200) ? null : (string) $body;
    }
    $ctx = stream_context_create(['http' => ['timeout' => TIMEOUT_S, 'ignore_errors' => true]]);
    $body = @file_get_contents($url, false, $ctx);
    return $body === false ? null : $body;
}

// --- Request -------------------------------------------------------------

$lat = filter_input(INPUT_GET, 'lat', FILTER_VALIDATE_FLOAT);
$lon = filter_input(INPUT_GET, 'lon', FILTER_VALIDATE_FLOAT);
if ($lat === false || $lat === null || $lon === false || $lon === null) {
    fail(400, 'lat and lon are required.');
}
if ($lat < BBOX['lat_min'] || $lat > BBOX['lat_max']
    || $lon < BBOX['lon_min'] || $lon > BBOX['lon_max']) {
    fail(400, 'Coordinates are outside this route.');
}

// Round to ~1km for the cache key so nearby requests share an entry. AirNow
// updates hourly, so a 10-minute cache costs nothing in freshness and keeps
// the per-key quota well clear of its limit.
$cacheKey = sprintf('%.2f,%.2f', $lat, $lon);
$cacheFile = sys_get_temp_dir() . '/airnow-' . hash('sha256', $cacheKey) . '.json';

if (is_readable($cacheFile) && (time() - filemtime($cacheFile)) < CACHE_TTL_S) {
    $cached = file_get_contents($cacheFile);
    if ($cached !== false) {
        header('X-Cache: HIT');
        echo $cached;
        exit;
    }
}

$url = AIRNOW_URL . '?' . http_build_query([
    'format' => 'application/json',
    'latitude' => sprintf('%.4f', $lat),
    'longitude' => sprintf('%.4f', $lon),
    'distance' => SEARCH_RADIUS_MI,
    'API_KEY' => api_key(),
]);

$body = http_get($url);
if ($body === null) {
    // AirNow rate-limits per key and stops returning data for the rest of the
    // hour once the cap is hit. A reading from a few minutes ago is far more
    // useful to someone checking for smoke than an empty panel, so fall back to
    // an expired cache entry, flagged as stale, before giving up.
    serve_stale_or_fail($cacheFile);
}

$observations = json_decode($body, true);
if (!is_array($observations)) {
    fail(502, 'Unexpected response from air quality service.');
}

// Drop the sentinel negatives AirNow uses for "no reading".
$observations = array_values(array_filter(
    $observations,
    static fn($o) => isset($o['AQI']) && is_numeric($o['AQI']) && $o['AQI'] >= 0
));
if ($observations === []) {
    fail(404, 'No air quality station reporting near this point.');
}

// AirNow reports each pollutant separately; the headline AQI is by convention
// the highest sub-index, so report that one and name the pollutant behind it.
usort($observations, static fn($a, $b) => $b['AQI'] <=> $a['AQI']);
$worst = $observations[0];

$distance = haversine_mi($lat, $lon, (float) $worst['Latitude'], (float) $worst['Longitude']);

$result = [
    'aqi' => (int) $worst['AQI'],
    'parameter' => $worst['ParameterName'],
    'category' => $worst['Category']['Name'],
    'reporting_area' => trim($worst['ReportingArea']) . ', ' . $worst['StateCode'],
    'distance_mi' => round($distance, 1),
    'far' => $distance > FAR_MI,
    'observed' => trim($worst['DateObserved']) . ' ' . sprintf('%02d:00', (int) $worst['HourObserved'])
        . ' ' . $worst['LocalTimeZone'],
    'fetched' => gmdate('c'),
    'all' => array_map(static fn($o) => [
        'parameter' => $o['ParameterName'],
        'aqi' => (int) $o['AQI'],
        'category' => $o['Category']['Name'],
    ], $observations),
];

$json = json_encode($result, JSON_UNESCAPED_SLASHES);
@file_put_contents($cacheFile, $json, LOCK_EX);
header('X-Cache: MISS');
echo $json;
