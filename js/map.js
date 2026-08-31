// Leaflet map init shared by day-N.html (window.__route/__towns/__waypoints/
// __options) and index.html (window.__days). Basemaps, the layer switcher and
// the click-to-hand-off popup all come from js/basemap.js.
(function () {
  var BASE = window.PCGF_BASEMAP;

  // The route line always matches the compare page's "our planned route"
  // color, not --color-theme (which is reserved for badge/CTA fills).
  function routeColor() {
    return getComputedStyle(document.documentElement).getPropertyValue("--color-route-planned").trim() || "#E8542B";
  }

  function altColor() {
    return getComputedStyle(document.documentElement).getPropertyValue("--color-route-reference").trim() || "#5C7A8A";
  }

  function initDayMap() {
    var el = document.getElementById("map");
    if (!el || !window.__route) return;

    var color = routeColor();
    var map = L.map(el, { scrollWheelZoom: false });
    BASE.addTiles(map);
    BASE.enablePointHandoff(map);

    var latlngs = window.__route;
    var line = L.polyline(latlngs, {
      color: color,
      weight: 4,
      lineCap: "round",
      lineJoin: "round",
    }).addTo(map);

    // Days still choosing between candidate tracks draw every option, so the
    // divergence is visible at a glance. The selected one is solid and on top;
    // the rest are dashed and muted. Selecting is preview only — it never
    // changes the day's official stats.
    var bounds = line.getBounds();
    var options = window.__options || [];
    var optionLines = [];

    if (options.length) {
      // The primary is already drawn as `line`; give the others their own
      // polyline so any of them can be promoted to selected.
      options.forEach(function (o) {
        if (!o.route) return;
        var lyr = o.primary ? line : L.polyline(o.route, {
          lineCap: "round",
          lineJoin: "round",
        }).addTo(map);
        lyr.bindTooltip(o.label + " — " + o.distance_mi + " mi", { sticky: true });
        optionLines[o.index] = lyr;
        bounds = bounds.extend(lyr.getBounds());
      });
    }

    function selectOption(index) {
      optionLines.forEach(function (lyr, i) {
        if (!lyr) return;
        var on = i === index;
        lyr.setStyle({
          color: on ? color : altColor(),
          weight: on ? 4 : 3,
          opacity: on ? 1 : 0.55,
          dashArray: on ? null : "6 6",
        });
        // Only meaningful once the map has a view; guarded so a call before
        // fitBounds can never abort init.
        if (on && lyr._map) lyr.bringToFront();
      });

      document.querySelectorAll("[data-elevation-for]").forEach(function (el) {
        el.hidden = Number(el.getAttribute("data-elevation-for")) !== index;
      });
      document.querySelectorAll("[data-route-option]").forEach(function (el) {
        el.classList.toggle(
          "route-option--selected",
          Number(el.getAttribute("data-route-option")) === index
        );
      });
    }

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

    // The night's hotel gets its own marker only when the route stops short of
    // the door — build_data.py sets gap_mi on exactly those days. On the rest
    // the hotel is the finish, within a couple of hundred feet, and a second
    // dot would land on top of the first: at any zoom that fits the day, the
    // two are the same pixel. There it names the finish marker instead.
    var hotel = window.__lodging;
    var hotelIsFinish = hotel && !hotel.gap_mi;

    L.circleMarker(end, {
      radius: 7,
      color: "#fff",
      weight: 2,
      fillColor: color,
      fillOpacity: 1,
    })
      .addTo(map)
      .bindPopup(hotelIsFinish
        ? window.__towns.end + " — " + hotel.name
        : window.__towns.end);

    if (hotel && hotel.gap_mi) {
      // Hollow rather than filled, so it reads as a destination rather than as
      // one more stop: every other dot on this map is solid.
      L.circleMarker([hotel.lat, hotel.lon], {
        radius: 6,
        color: "#E8542B",
        weight: 3,
        fillColor: "#fff",
        fillOpacity: 1,
      })
        .addTo(map)
        .bindPopup(hotel.name);
    }

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

    map.fitBounds(bounds, { padding: [20, 20] });

    // Wire the option switcher only after fitBounds has given the map a view —
    // Leaflet creates path renderers lazily, so styling or reordering a line
    // before then throws and would abort the rest of this function.
    if (options.length) {
      document.querySelectorAll("[data-route-toggle]").forEach(function (input) {
        input.addEventListener("change", function () {
          if (input.checked) selectOption(Number(input.value));
        });
      });
      var checked = document.querySelector("[data-route-toggle]:checked");
      selectOption(checked ? Number(checked.value) : 0);
    }

    BASE.observeResize(el, map, function () {
      return bounds;
    });
  }

  function initOverviewMap() {
    var el = document.getElementById("map");
    if (!el || !window.__days) return;

    var color = routeColor();
    var map = L.map(el, { scrollWheelZoom: false });
    BASE.addTiles(map);
    BASE.enablePointHandoff(map);

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
    BASE.observeResize(el, map, function () {
      return group.getBounds();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initDayMap();
    initOverviewMap();
  });
})();
