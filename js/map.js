// Leaflet map init shared by day-N.html (window.__route/__towns/__waypoints)
// and index.html (window.__days). CARTO Positron tiles (no API key).
(function () {
  var TILE_URL = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png";
  var TILE_ATTRIBUTION =
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors ' +
    '&copy; <a href="https://carto.com/attributions">CARTO</a>';

  // The route line always matches the compare page's "our planned route"
  // color, not --color-theme (which is reserved for badge/CTA fills).
  function routeColor() {
    return getComputedStyle(document.documentElement).getPropertyValue("--color-route-planned").trim() || "#E8542B";
  }

  function addTiles(map) {
    L.tileLayer(TILE_URL, {
      attribution: TILE_ATTRIBUTION,
      subdomains: "abcd",
      maxZoom: 19,
    }).addTo(map);
  }

  // The map card can grow after Leaflet has already measured it. Keep
  // Leaflet's cached size in sync whenever the container's box changes.
  function observeResize(el, map) {
    if (typeof ResizeObserver === "undefined") return;
    new ResizeObserver(function () {
      map.invalidateSize();
    }).observe(el);
  }

  function initDayMap() {
    var el = document.getElementById("map");
    if (!el || !window.__route) return;

    var color = routeColor();
    var map = L.map(el, { scrollWheelZoom: false });
    addTiles(map);
    observeResize(el, map);

    var latlngs = window.__route;
    var line = L.polyline(latlngs, {
      color: color,
      weight: 4,
      lineCap: "round",
      lineJoin: "round",
    }).addTo(map);

    var start = latlngs[0];
    var end = latlngs[latlngs.length - 1];

    L.circleMarker(start, {
      radius: 7,
      color: "#fff",
      weight: 2,
      fillColor: "#5C7A8A",
      fillOpacity: 1,
    })
      .addTo(map)
      .bindPopup(window.__towns.start);

    L.circleMarker(end, {
      radius: 7,
      color: "#fff",
      weight: 2,
      fillColor: color,
      fillOpacity: 1,
    })
      .addTo(map)
      .bindPopup(window.__towns.end);

    (window.__waypoints || []).forEach(function (w) {
      L.circleMarker([w.lat, w.lon], {
        radius: 5,
        color: "#fff",
        weight: 1.5,
        fillColor: "#2B2E33",
        fillOpacity: 1,
      })
        .addTo(map)
        .bindPopup(w.name);
    });

    map.fitBounds(line.getBounds(), { padding: [20, 20] });
  }

  function initOverviewMap() {
    var el = document.getElementById("map");
    if (!el || !window.__days) return;

    var color = routeColor();
    var map = L.map(el, { scrollWheelZoom: false });
    addTiles(map);
    observeResize(el, map);

    var group = L.featureGroup().addTo(map);

    window.__days.forEach(function (d) {
      if (!d.route) return;
      L.polyline(d.route, {
        color: color,
        weight: 3,
        opacity: 0.85,
        lineCap: "round",
        lineJoin: "round",
      }).addTo(group);

      var start = d.route[0];
      L.circleMarker(start, {
        radius: 5,
        color: "#fff",
        weight: 1.5,
        fillColor: "#5C7A8A",
        fillOpacity: 1,
      })
        .addTo(group)
        .bindTooltip("Day " + d.day + " — " + d.towns.start, { direction: "top" });
    });

    map.fitBounds(group.getBounds(), { padding: [20, 20] });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initDayMap();
    initOverviewMap();
  });
})();
