// Compare page: two toggleable Leaflet layers — our day-by-day planned
// route (window.__planned_days) and the Adventure Cycling reference track
// (window.__reference_route) — so it's easy to see where they diverge.
(function () {
  var BASE = window.PCGF_BASEMAP;

  function cssVar(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  document.addEventListener("DOMContentLoaded", function () {
    var el = document.getElementById("map");
    if (!el || !window.__reference_route) return;

    var plannedColor = cssVar("--color-route-planned", "#E8542B");
    var referenceColor = cssVar("--color-route-reference", "#FF2E88");

    var map = L.map(el, { scrollWheelZoom: false });
    BASE.addTiles(map);
    BASE.observeResize(el, map);
    BASE.enablePointHandoff(map);

    var plannedLayer = L.layerGroup();
    (window.__planned_days || []).forEach(function (d) {
      if (!d.route) return;
      L.polyline(d.route, {
        color: plannedColor,
        weight: 4,
        opacity: 0.95,
        lineCap: "round",
        lineJoin: "round",
      }).addTo(plannedLayer);

      var start = d.route[0];
      L.circleMarker(start, {
        radius: 5,
        color: "#fff",
        weight: 1.5,
        fillColor: plannedColor,
        fillOpacity: 1,
      })
        .addTo(plannedLayer)
        .bindTooltip("Day " + d.day + " — " + d.towns.start, { direction: "top" });
    });

    var referenceLayer = L.layerGroup();
    L.polyline(window.__reference_route, {
      color: referenceColor,
      weight: 3,
      opacity: 0.9,
      dashArray: "1,8",
      lineCap: "round",
      lineJoin: "round",
    }).addTo(referenceLayer);

    plannedLayer.addTo(map);
    referenceLayer.addTo(map);

    var bounds = L.latLngBounds(window.__reference_route);
    (window.__planned_days || []).forEach(function (d) {
      if (d.route) bounds.extend(d.route);
    });
    map.fitBounds(bounds, { padding: [24, 24] });

    var plannedToggle = document.getElementById("toggle-planned");
    var referenceToggle = document.getElementById("toggle-reference");

    if (plannedToggle) {
      plannedToggle.addEventListener("change", function () {
        if (plannedToggle.checked) {
          map.addLayer(plannedLayer);
        } else {
          map.removeLayer(plannedLayer);
        }
      });
    }

    if (referenceToggle) {
      referenceToggle.addEventListener("change", function () {
        if (referenceToggle.checked) {
          map.addLayer(referenceLayer);
        } else {
          map.removeLayer(referenceLayer);
        }
      });
    }
  });
})();
