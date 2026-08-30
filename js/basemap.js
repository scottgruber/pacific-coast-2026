// Shared Leaflet basemap setup for every map on the site (day pages, the
// overview, and the compare page), so the tile source and the layer switcher
// are defined once.
//
// Tiles come from Mapbox's Static Tiles API as raster tiles, which keeps the
// site on plain Leaflet instead of pulling in Mapbox GL JS. Three styles are
// offered because the two things this trip actually needs to eyeball are shade
// and climbing:
//   Plain      — light basemap, easiest for reading town and road names.
//   Vegetation — satellite imagery, where tree cover is directly visible.
//   Elevation  — Mapbox Outdoors, which carries contour lines and hillshade.
//
// With no token configured in js/config.js, this falls back to CARTO's keyless
// tiles so the site still renders — just on the rate-limited basemap.
window.PCGF_BASEMAP = (function () {
  var OSM_ATTR =
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
  var CARTO_ATTR = OSM_ATTR + ' &copy; <a href="https://carto.com/attributions">CARTO</a>';
  var MAPBOX_ATTR = '&copy; <a href="https://www.mapbox.com/about/maps/">Mapbox</a> ' + OSM_ATTR;

  // Elevation is the default: contours and hillshade are what this route is
  // actually being judged on, and it still carries road and town labels.
  var STYLES = [
    { label: "Elevation", style: "mapbox/outdoors-v12", default: true },
    { label: "Vegetation", style: "mapbox/satellite-streets-v12" },
    { label: "Plain", style: "mapbox/light-v11" },
  ];

  function token() {
    return (window.PCGF_CONFIG || {}).MAPBOX_TOKEN || "";
  }

  function mapboxLayer(style) {
    // 512px tiles with zoomOffset -1 render at the same scale as standard
    // 256px tiles while halving the number of tile requests.
    return L.tileLayer(
      "https://api.mapbox.com/styles/v1/" + style +
        "/tiles/512/{z}/{x}/{y}{r}?access_token=" + encodeURIComponent(token()),
      { attribution: MAPBOX_ATTR, tileSize: 512, zoomOffset: -1, maxZoom: 19 }
    );
  }

  function cartoLayer() {
    return L.tileLayer(
      "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
      { attribution: CARTO_ATTR, subdomains: "abcd", maxZoom: 19 }
    );
  }

  // Adds the basemap to `map`. Returns the {label: layer} basemap dictionary
  // when Mapbox is configured (so a caller with its own overlays can fold the
  // basemaps into a single layer control), or null on the CARTO fallback.
  //
  // Pass addControl:false to get the layers back without a control attached.
  function addTiles(map, opts) {
    opts = opts || {};
    if (!token()) {
      cartoLayer().addTo(map);
      return null;
    }
    var layers = {};
    STYLES.forEach(function (s) {
      var layer = mapboxLayer(s.style);
      layers[s.label] = layer;
      if (s.default) layer.addTo(map);
    });
    if (opts.addControl !== false) {
      L.control.layers(layers, null, { position: "topright" }).addTo(map);
    }
    return layers;
  }

  // Apple and Google both accept a plain "lat,lon" query string, so a dropped
  // pin can hand off to either without an API key. This stands in for an
  // embedded Street View / Look Around, which neither provider exposes to a
  // third-party site without a paid key.
  function handoffPopupHtml(lat, lon) {
    var ll = lat.toFixed(5) + "," + lon.toFixed(5);
    return (
      '<div class="map-handoff">' +
      '<p class="map-handoff__coords">' + ll + "</p>" +
      '<a href="https://www.google.com/maps/search/?api=1&query=' + ll +
        '" target="_blank" rel="noopener">Google Maps</a>' +
      '<a href="https://maps.apple.com/?q=' + ll +
        '" target="_blank" rel="noopener">Apple Maps</a>' +
      '<a href="https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=' + ll +
        '" target="_blank" rel="noopener">Street View</a>' +
      "</div>"
    );
  }

  // Click anywhere on the map to drop a pin and hand that point off to a
  // full-featured map app.
  function enablePointHandoff(map) {
    map.on("click", function (e) {
      L.popup()
        .setLatLng(e.latlng)
        .setContent(handoffPopupHtml(e.latlng.lat, e.latlng.lng))
        .openOn(map);
    });
  }

  // The map card can grow after Leaflet has already measured it — the card is
  // a flex column, so the map's final height isn't known until the buttons and
  // route options below it have laid out. Keep Leaflet's cached size in sync,
  // and re-fit afterwards: invalidateSize() preserves the current zoom, so a
  // map that first measured near-zero height would otherwise stay stuck at the
  // very low zoom its initial fitBounds picked.
  //
  // getBounds is optional; pass it on maps that fit to a route.
  function observeResize(el, map, getBounds) {
    if (typeof ResizeObserver === "undefined") return;
    new ResizeObserver(function () {
      map.invalidateSize();
      if (getBounds) map.fitBounds(getBounds(), { padding: [20, 20] });
    }).observe(el);
  }

  return {
    addTiles: addTiles,
    enablePointHandoff: enablePointHandoff,
    observeResize: observeResize,
    handoffPopupHtml: handoffPopupHtml,
  };
})();
